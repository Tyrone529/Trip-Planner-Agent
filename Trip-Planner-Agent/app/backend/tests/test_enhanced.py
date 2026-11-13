import asyncio,copy,json,sys
from pathlib import Path
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.upgrade.models import PlanRequest,PatchRequest,Constraints
from app.upgrade.store import Store,VersionConflict
from app.upgrade.engine import Planner
from app.upgrade.tools import ToolExecutor,ToolError,SearchArgs
from app.upgrade.validation import validate
from app.upgrade.providers import MCPProvider,AmapProvider
from app.upgrade.api import create_app

async def complete(planner,request,old=None,patch=None):
    r=planner.start(request,old,patch)
    for _ in range(300):
        x=planner.store.get_run(r['id'])
        if x['status']!='running':return x,planner.store.get(x['plan_id']) if x['status']=='completed' else None
        await asyncio.sleep(.01)
    raise AssertionError('run did not finish')
def request(**kwargs):return PlanRequest(start_date='2026-11-05',end_date='2026-11-07',**kwargs)
@pytest.fixture
def planner(tmp_path):return Planner(Store(tmp_path/'trips.db'))

@pytest.mark.parametrize('days',[1,2,3,4,5,6,7])
@pytest.mark.asyncio
async def test_valid_generation(planner,days):
    from datetime import date,timedelta
    req=PlanRequest(start_date='2026-11-05',end_date=(date(2026,11,5)+timedelta(days=days-1)).isoformat(),travel_days=days)
    run,p=await complete(planner,req)
    assert run['status']=='completed';assert len(p['days'])==days
    assert not [i for i in p['validation'] if i['severity']=='error']
    assert all(a['poi']['id'].startswith('p') for d in p['days'] for a in d['activities'])

@pytest.mark.parametrize('end',['08:00','00:00','09:00'])
def test_invalid_windows(end):
    with pytest.raises(ValidationError):Constraints(day_start='09:00',day_end=end)
@pytest.mark.parametrize('value',[0,-1,-100])
def test_invalid_budgets(value):
    with pytest.raises(ValidationError):Constraints(max_budget=value)
def test_dates_must_match():
    with pytest.raises(ValidationError):PlanRequest(start_date='2026-11-05',end_date='2026-11-06',travel_days=3)

@pytest.mark.parametrize('end',['11:00','11:30','12:00'])
@pytest.mark.asyncio
async def test_repair_respects_deadline(planner,end):
    r,p=await complete(planner,request(constraints=Constraints(day_end=end)))
    assert not any(i['code']=='DEADLINE' for i in p['validation'])
    assert any(e['stage']=='repair' for e in r['events'])
@pytest.mark.asyncio
async def test_impossible_window_is_conflict(planner):
    _,p=await complete(planner,request(constraints=Constraints(day_end='09:30')))
    assert p['status']=='conflicts'
@pytest.mark.asyncio
async def test_low_budget_is_not_silently_relaxed(planner):
    _,p=await complete(planner,request(constraints=Constraints(max_budget=10)))
    assert p['request']['constraints']['max_budget']==10
    assert any(i['code']=='BUDGET' for i in p['validation'])
@pytest.mark.asyncio
async def test_required_missing_is_conflict(planner):
    _,p=await complete(planner,request(constraints=Constraints(must_visit=['不存在的地点'])))
    assert any(i['code']=='MUST_VISIT' for i in p['validation'])
@pytest.mark.asyncio
async def test_revision_preserves_other_days_and_locks(planner):
    _,p=await complete(planner,request())
    p['days'][1]['hotel_locked']=True;p['days'][1]['activities'][0]['locked']=True
    p=planner.store.save(p,p['version']);before=copy.deepcopy(p)
    _,changed=await complete(planner,request(),p,PatchRequest(expected_version=p['version'],day_index=1,action='relaxed'))
    assert changed['days'][0]==before['days'][0];assert changed['days'][2]==before['days'][2]
    assert changed['days'][1]['hotel']==before['days'][1]['hotel']
    assert before['days'][1]['activities'][0] in changed['days'][1]['activities']
    assert changed['version']==p['version']+1
@pytest.mark.asyncio
async def test_indoor_conflicting_lock_reported(planner):
    _,p=await complete(planner,request());p['days'][0]['activities'][0]['locked']=True
    p=planner.store.save(p,p['version'])
    _,q=await complete(planner,request(),p,PatchRequest(expected_version=p['version'],day_index=0,action='indoor'))
    assert any(i['code']=='INDOOR_CONFLICT' for i in q['validation'])
@pytest.mark.asyncio
async def test_cache_preserves_source_time(planner):
    _,p=await complete(planner,request());_,q=await complete(planner,request())
    assert any(t['status']=='cache_hit' for t in q['trace'])
    assert p['days'][0]['activities'][0]['poi']['source']==q['days'][0]['activities'][0]['poi']['source']
@pytest.mark.asyncio
async def test_no_results_produces_no_fake_plan(planner):
    r,p=await complete(planner,request(city='上海'))
    assert r['status']=='failed';assert r['error']['code']=='NO_RESULTS';assert p is None;assert planner.store.list()==[]
def test_versions_survive_new_store(tmp_path):
    path=tmp_path/'p.db';s=Store(path);p=s.save({'city':'北京'});p=s.save(p,p['version'])
    assert Store(path).get(p['id'])['version']==2
    with pytest.raises(VersionConflict):s.save(p,1)

@pytest.mark.asyncio
async def test_transient_retry_then_success():
    count=0;trace=[]
    async def fn(**kwargs):
        nonlocal count;count+=1
        if count==1:raise ToolError('NETWORK','暂时故障',True)
        return [{'id':'real'}]
    x=await ToolExecutor().call('test',{'city':'北京','keywords':'景点'},SearchArgs,fn,trace)
    assert x==[{'id':'real'}];assert trace[0]['attempts']==2
@pytest.mark.asyncio
async def test_timeout_bounded():
    async def fn(**kwargs):await asyncio.sleep(1)
    trace=[]
    with pytest.raises(ToolError) as e:await ToolExecutor(timeout=.005,retries=1).call('t',{'city':'北京','keywords':'景点'},SearchArgs,fn,trace)
    assert e.value.code=='TIMEOUT';assert trace[0]['attempts']==2
@pytest.mark.asyncio
async def test_permanent_error_not_retried():
    async def fn(**kwargs):raise ToolError('NO_RESULTS','无结果')
    trace=[]
    with pytest.raises(ToolError):await ToolExecutor().call('t',{'city':'北京','keywords':'景点'},SearchArgs,fn,trace)
    assert trace[0]['attempts']==1
@pytest.mark.asyncio
async def test_bad_arguments_never_execute():
    async def fn(**kwargs):raise AssertionError('should not call')
    with pytest.raises(ToolError) as e:await ToolExecutor().call('t',{'city':'','keywords':'景点'},SearchArgs,fn,[])
    assert e.value.code=='INVALID_ARGUMENTS'
@pytest.mark.asyncio
async def test_mcp_real_stdio_transport():
    async with MCPProvider(fixture=True) as provider:
        data=await provider.search(city='北京',keywords='景点',kind='attraction')
        assert len(data)>0 and data[0]['source']['status']=='fixture'

def test_api_rejects_stale_lock_and_bad_day(tmp_path):
    app=create_app(tmp_path/'api.db')
    with TestClient(app) as c:
        p=c.post('/api/trip/plan',json=request().model_dump(mode='json')).json()['data']
        path='/api/plans/'+p['id']+'/lock'
        assert c.post(path,json={'expected_version':1,'day_index':9}).status_code==422
        assert c.post(path,json={'expected_version':1,'day_index':0,'locked':True}).status_code==200
        assert c.post(path,json={'expected_version':1,'day_index':0,'locked':True}).status_code==409
        assert c.get('/api/plans/'+p['id']+'?version=1').json()['version']==1

@pytest.mark.asyncio
async def test_target_day_deadline_does_not_change_other_days(planner):
    _,p=await complete(planner,request())
    _,q=await complete(planner,request(),p,PatchRequest(expected_version=1,day_index=1,action='constraints',day_end='11:30'))
    assert q['days'][0]==p['days'][0] and q['days'][2]==p['days'][2]
    assert q['days'][1]['deadline']=='11:30'
    assert not any(x['code']=='DEADLINE' for x in q['validation'])

@pytest.mark.asyncio
async def test_failed_retry_reuses_checkpoint(planner,monkeypatch):
    original=planner.select
    async def fail(*args,**kwargs):raise ToolError('TEMPORARY','注入的规划失败',True)
    monkeypatch.setattr(planner,'select',fail)
    r,_=await complete(planner,request())
    assert r['status']=='failed' and r['artifacts']
    monkeypatch.setattr(planner,'select',original)
    run=planner.start(request(),artifacts=r['artifacts'])
    for _ in range(200):
        out=planner.store.get_run(run['id'])
        if out['status']!='running':break
        await asyncio.sleep(.01)
    assert out['status']=='completed'
    assert any(t['status']=='checkpoint_hit' for t in out['trace'])

def test_restart_marks_unfinished_run(tmp_path):
    s=Store(tmp_path/'restart.db');s.save_run({'id':'pending','status':'running','request':{}})
    s.recover_interrupted();assert s.get_run('pending')['status']=='interrupted'

@pytest.mark.asyncio
async def test_mcp_error_keeps_error_category():
    async with MCPProvider(fixture=True) as provider:
        with pytest.raises(ToolError) as e:await provider.search(city='上海',keywords='景点',kind='attraction')
        assert e.value.code=='NO_RESULTS' and not e.value.retryable

@pytest.mark.asyncio
async def test_llm_cannot_invent_candidate(planner,monkeypatch):
    import httpx
    class FakeClient:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'days':[{'day_index':0,'poi_ids':['invented-place']}]})}}]})
    monkeypatch.setenv('LLM_API_KEY','test-only')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:FakeClient())
    req=request(mode='live');run={'llm':[]}
    with pytest.raises(ToolError) as e:await planner.select(req,[{'id':'real-place'}],[0],run,None,None)
    assert e.value.code=='LLM_OUTPUT'

@pytest.mark.asyncio
async def test_concurrent_revisions_cannot_overwrite(planner):
    _,p=await complete(planner,request())
    patch=PatchRequest(expected_version=1,day_index=1,action='relaxed')
    a,b=await asyncio.gather(complete(planner,request(),p,patch),complete(planner,request(),p,patch))
    assert sorted([a[0]['status'],b[0]['status']])==['completed','failed']
    assert planner.store.get(p['id'])['version']==2

@pytest.mark.asyncio
async def test_locked_anchor_uses_new_route_evidence(planner):
    _,p=await complete(planner,request());q=copy.deepcopy(p)
    a=q['days'][0]['activities'][0];a['locked']=True
    q['days'][0]['route_checks'][a['id']]['minutes']=400
    issues,_=validate(q,request())
    assert any(i['code']=='TIME_CONFLICT' for i in issues)

@pytest.mark.asyncio
async def test_live_missing_model_key_is_explicit(planner,monkeypatch):
    monkeypatch.delenv('LLM_API_KEY',raising=False)
    r,p=await complete(planner,request(mode='live'))
    assert p is None and r['error']['code']=='CONFIGURATION' and r['trace']==[]

@pytest.mark.asyncio
async def test_remote_mcp_search_hydrates_coordinates(monkeypatch):
    provider=MCPProvider();provider.remote_url='https://example.invalid/sse'
    calls=[]
    async def call(name,args):
        calls.append(name)
        if name=='maps_text_search':return {'pois':[{'id':'real1','name':'博物馆'}]}
        return {'id':'real1','name':'博物馆','location':'116.4,39.9','address':'北京'}
    monkeypatch.setattr(provider,'_call',call)
    places=await provider.search('北京','历史文化')
    assert calls==['maps_text_search','maps_search_detail']
    assert places[0]['longitude']==116.4 and places[0]['price'] is None
    assert places[0]['source']['status']=='observed'

@pytest.mark.asyncio
async def test_remote_mcp_missing_location_never_fabricates(monkeypatch):
    provider=MCPProvider();provider.remote_url='https://example.invalid/sse'
    async def call(name,args):
        return {'pois':[{'id':'1','name':'无坐标地点'}]} if name=='maps_text_search' else {'name':'无坐标地点'}
    monkeypatch.setattr(provider,'_call',call)
    assert await provider.search('北京','景点')==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('mode,raw,minutes,km',[
 ('transit',{'route':{'distance':'3000','transits':[{'duration':'1200'}]}},20,3),
 ('walking',{'route':{'paths':[{'duration':'600','distance':'700'}]}},10,.7),
 ('driving',{'route':{'paths':[{'cost':{'duration':'121'},'distance':'1200'}]}},3,1.2)])
async def test_remote_mcp_route_shapes(monkeypatch,mode,raw,minutes,km):
    provider=MCPProvider();provider.remote_url='https://example.invalid/sse'
    async def call(name,args):return raw
    monkeypatch.setattr(provider,'_call',call)
    result=await provider.route('116,39','117,40','北京',mode)
    assert result['minutes']==minutes and result['km']==km

@pytest.mark.asyncio
@pytest.mark.parametrize('lock_count',[0,1,2])
async def test_relaxed_caps_activities_even_when_selector_returns_more(planner,monkeypatch,lock_count):
    _,old=await complete(planner,request())
    for a in old['days'][1]['activities'][:lock_count]:a['locked']=True
    old=planner.store.save(old,old['version'])
    before=copy.deepcopy(old)
    async def excessive_selection(req,places,indexes,*args):
        return {i:[p['id'] for p in places[:3]] for i in indexes}
    monkeypatch.setattr(planner,'select',excessive_selection)
    _,new=await complete(planner,request(),old,PatchRequest(expected_version=old['version'],day_index=1,action='relaxed'))
    assert len(new['days'][1]['activities'])==max(1,lock_count)
    assert all(a in new['days'][1]['activities'] for a in before['days'][1]['activities'] if a['locked'])
    assert new['days'][0]==before['days'][0] and new['days'][2]==before['days'][2]
