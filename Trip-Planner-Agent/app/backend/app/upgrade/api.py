import asyncio,copy,os
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2]/'.env',override=False)
from fastapi import FastAPI,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .models import PlanRequest,PatchRequest,LockRequest
from .store import Store,VersionConflict
from .engine import Planner
from .tools import ToolError

@asynccontextmanager
async def lifespan(app):
    yield
    for task in list(app.state.planner.background):task.cancel()
    if app.state.planner.background:await asyncio.gather(*app.state.planner.background,return_exceptions=True)

def create_app(db_path=None):
    app=FastAPI(title='旅行规划与动态调整',version='2.0.0',lifespan=lifespan)
    store=Store(db_path);store.recover_interrupted();planner=Planner(store);app.state.store=store;app.state.planner=planner
    app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173','http://localhost:5174','http://127.0.0.1:5174'],allow_methods=['GET','POST'],allow_headers=['Content-Type'])
    def get(id,version=None):
        try:return store.get(id,version)
        except KeyError:raise HTTPException(404,'行程不存在')
    @app.get('/health')
    async def health():return {'status':'healthy','version':'2.0.0','live_map_configured':bool(os.getenv('AMAP_API_KEY')),'llm_configured':bool(os.getenv('LLM_API_KEY')),'remote_mcp_configured':bool(os.getenv('AMAP_MCP_URL'))}
    @app.post('/api/runs',status_code=202)
    async def start(req:PlanRequest):return planner.start(req)
    @app.get('/api/runs/{id}')
    async def run(id:str):
        try:return store.get_run(id)
        except KeyError:raise HTTPException(404,'任务不存在')
    @app.post('/api/runs/{id}/retry',status_code=202)
    async def retry(id:str):
        try:prior=store.get_run(id)
        except KeyError:raise HTTPException(404,'任务不存在')
        if prior['status'] not in ['failed','interrupted']:raise HTTPException(409,'仅失败或中断任务可以重试')
        old=prior.get('previous')
        if old and get(old['id'])['version']!=old['version']:raise HTTPException(409,'行程已有新版本，请刷新后重新修改')
        req=PlanRequest.model_validate(prior['request'])
        patch=PatchRequest.model_validate(prior['patch']) if prior.get('patch') else None
        return planner.start(req,old,patch,prior.get('artifacts'))
    @app.get('/api/plans')
    async def plans():return [{'id':p['id'],'version':p['version'],'city':p['city'],'updated_at':p['updated_at'],'status':p['status'],'mode':p['mode']} for p in store.list()]
    @app.get('/api/plans/{id}')
    async def plan(id:str,version:int|None=None):return get(id,version)
    @app.get('/api/plans/{id}/versions')
    async def history(id:str):
        get(id);return store.history(id)
    @app.post('/api/plans/{id}/revise',status_code=202)
    async def revise(id:str,patch:PatchRequest):
        old=get(id)
        if patch.expected_version!=old['version']:raise HTTPException(409,'行程已有新版本，请刷新')
        if patch.day_index>=len(old['days']):raise HTTPException(422,'指定日期超出行程范围')
        req=PlanRequest.model_validate(old['request'])
        if patch.constraints:req.constraints=patch.constraints
        return planner.start(req,old,patch)
    @app.post('/api/plans/{id}/lock')
    async def lock(id:str,req:LockRequest):
        old=get(id)
        if req.expected_version!=old['version']:raise HTTPException(409,'行程已有新版本，请刷新')
        if req.day_index>=len(old['days']):raise HTTPException(422,'指定日期超出行程范围')
        new=copy.deepcopy(old);day=new['days'][req.day_index]
        if req.activity_id:
            activity=next((a for a in day['activities'] if a['id']==req.activity_id),None)
            if activity is None:raise HTTPException(404,'活动不存在')
            activity['locked']=req.locked
        else:
            if day.get('hotel') is None:raise HTTPException(422,'暂无住宿可锁定')
            day['hotel_locked']=req.locked
        new['diff']=[]
        try:return store.save(new,req.expected_version)
        except VersionConflict as e:raise HTTPException(409,str(e))
    @app.post('/api/trip/plan')
    async def compatibility(req:PlanRequest):
        r=planner.start(req)
        while (run:=store.get_run(r['id']))['status']=='running':await asyncio.sleep(.05)
        if run['status']=='failed':raise HTTPException(424,run['error'])
        p=copy.deepcopy(store.get(run['plan_id']))
        p.update(start_date=p['request']['start_date'],end_date=p['request']['end_date'],weather_info=[],overall_suggestions='此计划由增强规划器生成，费用为部分估算；详细校验和来源请在行程工作台查看。')
        for d in p['days']:
            d.update(description='第'+str(d['day_index']+1)+'天',transportation=p['request']['transportation'],accommodation=p['request']['accommodation'],meals=[])
            d['attractions']=[{**a['poi'],'location':{'longitude':a['poi']['longitude'],'latitude':a['poi']['latitude']},'visit_duration':a['poi']['duration_min'],'description':a['poi']['source']['detail'],'ticket_price':a['poi']['price'] or 0} for a in d['activities']]
            if d.get('hotel'):
                h=d['hotel'];h.update(location={'longitude':h['longitude'],'latitude':h['latitude']},price_range='待确认',estimated_cost=h['price'] or 0,rating='',distance='',type='住宿候选')
        p['budget'].update(total=p['budget']['subtotal'],total_attractions=sum(a['poi']['price'] or 0 for d in p['days'] for a in d['activities']),total_hotels=sum((d.get('hotel') or {}).get('price') or 0 for d in p['days'][:-1]),total_meals=sum(d['meal_budget'] for d in p['days']),total_transportation=0)
        return {'success':True,'data':p,'run_id':r['id']}
    return app
