import asyncio,json,math,os,sys
from contextlib import AsyncExitStack
from datetime import date,timedelta
from pathlib import Path
import httpx
from .models import Place,Source,utcnow
from .tools import ToolError

# Deliberately authored replay data, NOT live prices, opening hours or navigation.
_FIXTURES=[
 ('p1','故宫博物院',116.397,39.918,False,60),('p2','景山公园',116.396,39.925,False,10),
 ('p3','中国国家博物馆',116.401,39.906,True,0),('p4','首都博物馆',116.342,39.906,True,0),
 ('p5','北京天文馆',116.337,39.938,True,45),('p6','中国美术馆',116.410,39.925,True,0),
 ('p7','天坛公园',116.411,39.881,False,34),('p8','北海公园',116.389,39.925,False,10),
 ('p9','北京自然博物馆',116.400,39.884,True,0),('p10','陶然亭公园',116.381,39.872,False,10),
 ('h1','示例住宿甲',116.400,39.915,True,320),('h2','示例住宿乙',116.395,39.920,True,260)]

class ReplayProvider:
    def __init__(self):
        self.places=[Place(id=i,name=n,address='北京离线测试地点',longitude=x,latitude=y,indoor=indoor,kind='hotel' if i.startswith('h') else 'attraction',price=price,price_status='fixture',source=Source(provider='人工编制的离线测试快照',status='fixture',detail='仅用于测试流程，费用和路线不是实时数据')).model_dump() for i,n,x,y,indoor,price in _FIXTURES]
    async def search(self,city,keywords,kind='attraction'):
        if city not in ['北京','北京市']:raise ToolError('NO_RESULTS','离线示例仅支持北京，请切换真实查询模式')
        data=[p for p in self.places if p['kind']==kind]
        matches=[p for p in data if keywords in p['name']]
        return matches or ([] if keywords not in ['景点','酒店','室内','历史文化','自然风光','休闲','艺术','美食','购物'] else data)
    async def weather(self,city):
        if city not in ['北京','北京市']:raise ToolError('NO_RESULTS','离线示例仅支持北京')
        return {'forecasts':[{'date':(date(2026,11,4)+timedelta(days=i)).isoformat(),'day_weather':'小雨' if i==2 else '晴'} for i in range(7)],'source':Source(provider='人工编制天气快照',status='fixture',detail='仅用于测试，不是天气预报').model_dump()}
    async def route(self,origin,destination,city,mode):
        a,b=([float(v) for v in s.split(',')] for s in [origin,destination])
        km=math.hypot((a[0]-b[0])*85,(a[1]-b[1])*111)*1.3
        speed={'walking':4.5,'driving':25,'transit':18}[mode]
        return {'minutes':max(5,round(km/speed*60+5)),'km':round(km,2),'source':Source(provider='离线几何路线估算',status='fixture',detail='不是导航结果，不含实时交通').model_dump()}

class AmapProvider:
    def __init__(self):
        self.key=os.getenv('AMAP_API_KEY','')
        if not self.key:raise ToolError('CONFIGURATION','请先在本地 .env 配置高德 Web 服务 Key')
    async def _get(self,path,**params):
        try:
            async with httpx.AsyncClient(timeout=10) as c:r=await c.get('https://restapi.amap.com'+path,params={'key':self.key,**params})
            if r.status_code in [429,500,502,503,504]:raise ToolError('UPSTREAM_BUSY','地图服务繁忙',True)
            if r.status_code!=200:raise ToolError('UPSTREAM_ERROR','地图服务返回异常')
            x=r.json()
        except httpx.TimeoutException:raise ToolError('TIMEOUT','地图查询超时',True)
        except httpx.HTTPError:raise ToolError('NETWORK','地图服务连接失败',True)
        if x.get('status')!='1':
            transient=x.get('infocode') in ['10003','10004','10016','10020']
            raise ToolError('AMAP_ERROR','高德查询失败，错误码 '+str(x.get('infocode','unknown')),transient)
        return x
    async def search(self,city,keywords,kind='attraction'):
        x=await self._get('/v3/place/text',city=city,keywords=keywords,citylimit='true',offset=20,extensions='all')
        result=[]
        for p in x.get('pois',[]):
            try:lon,lat=map(float,p['location'].split(','))
            except (KeyError,ValueError):continue
            result.append(Place(id=p['id'],name=p['name'],address=p.get('address') if isinstance(p.get('address'),str) else city,longitude=lon,latitude=lat,kind=kind,source=Source(provider='高德 Web 服务 POI',status='observed',detail='位置来自查询；价格、房态和开放时间未核实')).model_dump())
        return result
    async def weather(self,city):
        district=await self._get('/v3/config/district',keywords=city,subdistrict=0)
        districts=district.get('districts',[])
        if not districts:raise ToolError('NO_RESULTS','未查到城市编码')
        x=await self._get('/v3/weather/weatherInfo',city=districts[0]['adcode'],extensions='all')
        return {'forecasts':[{'date':p['date'],'day_weather':p['dayweather']} for f in x.get('forecasts',[]) for p in f.get('casts',[])],'source':Source(provider='高德天气',status='observed').model_dump()}
    async def route(self,origin,destination,city,mode):
        if mode=='transit':
            x=await self._get('/v3/direction/transit/integrated',origin=origin,destination=destination,city=city,cityd=city)
            paths=x.get('route',{}).get('transits',[])
        else:
            x=await self._get('/v3/direction/'+('walking' if mode=='walking' else 'driving'),origin=origin,destination=destination)
            paths=x.get('route',{}).get('paths',[])
        if not paths:raise ToolError('NO_ROUTE','没有查到可用路线')
        p=paths[0]
        return {'minutes':math.ceil(float(p['duration'])/60),'km':round(float(p.get('distance',0))/1000,2),'source':Source(provider='高德路线',status='observed',detail='路线耗时为查询估算，实际出行需留缓冲').model_dump()}

class MCPProvider:
    """A session-owned MCP connection; close in the creating task to avoid cancel-scope leaks."""
    def __init__(self,fixture=False):self.fixture=fixture;self.stack=AsyncExitStack();self.session=None;self.remote_url=os.getenv("AMAP_MCP_URL", "") if not fixture else ""
    async def __aenter__(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        try:
            async with asyncio.timeout(25):
                if self.remote_url:
                    from mcp.client.sse import sse_client
                    transport=await self.stack.enter_async_context(sse_client(self.remote_url,timeout=15,sse_read_timeout=45))
                else:
                    env={k:v for k,v in os.environ.items() if k not in ['LLM_API_KEY','OPENAI_API_KEY']}
                    env['TRIP_MCP_FIXTURE']='1' if self.fixture else '0'
                    transport=await self.stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,args=['-m','app.upgrade.mcp_server'],env=env,cwd=str(Path(__file__).resolve().parents[2]))))
                self.session=await self.stack.enter_async_context(ClientSession(*transport,read_timeout_seconds=timedelta(seconds=20)));await self.session.initialize()
                if self.remote_url:
                    names={t.name for t in (await self.session.list_tools()).tools}
                    required={'maps_text_search','maps_search_detail','maps_weather','maps_direction_walking','maps_direction_driving','maps_direction_transit_integrated'}
                    if not required<=names:raise ToolError('MCP_SCHEMA','远程地图 MCP 缺少必要工具')
                return self
        except BaseException as exc:
            await self.stack.aclose()
            if isinstance(exc,asyncio.CancelledError):raise
            if isinstance(exc,ToolError):raise
            raise ToolError('MCP_CONNECTION','远程 MCP 连接或初始化失败，请稍后重试',True) from None
    async def __aexit__(self,*args):await self.stack.__aexit__(*args)
    async def _call(self,name,args):
        r=await self.session.call_tool(name,args)
        if r.isError:raise ToolError('MCP_ERROR','MCP 工具执行失败')
        texts=[c.text for c in r.content if getattr(c,'type','')=='text']
        try:data=json.loads(''.join(texts))
        except ValueError:raise ToolError('INVALID_OUTPUT','MCP 工具返回格式不合法')
        if isinstance(data,dict) and 'error' in data:
            e=data['error']
            if isinstance(e,dict):raise ToolError(e.get('code','MCP_ERROR'),e.get('message','MCP 工具执行失败'),e.get('retryable',False))
            raise ToolError('MCP_ERROR','MCP 上游返回错误')
        return data
    async def search(self,city,keywords,kind='attraction'):
        if not self.remote_url:return await self._call('search_poi',{'city':city,'keywords':keywords,'kind':kind})
        keywords={'历史文化':'博物馆','自然风光':'公园','艺术':'美术馆','休闲':'景点'}.get(keywords,keywords)
        result=await self._call('maps_text_search',{'city':city,'keywords':keywords,'types':'100000' if kind=='hotel' else '110000|140100'})
        candidates=result.get('pois',[])[:6]
        async def detail(p):
            try:
                d=p if p.get('location') else await self._call('maps_search_detail',{'id':p['id']})
                if isinstance(d.get('pois'),list):d=d['pois'][0] if d['pois'] else {}
                lon,lat=map(float,d['location'].split(','))
                return Place(id=p['id'],name=d.get('name',p['name']),address=d.get('address') if isinstance(d.get('address'),str) else city,longitude=lon,latitude=lat,kind=kind,source=Source(provider='高德远程 MCP',status='observed',detail='搜索及POI详情查询；价格、房态和营业时间未核实')).model_dump()
            except (ToolError,KeyError,ValueError,TypeError):return None
        return [p for p in await asyncio.gather(*(detail(p) for p in candidates)) if p]
    async def route(self,origin,destination,city,mode):
        if not self.remote_url:return await self._call('route',{'origin':origin,'destination':destination,'city':city,'mode':mode})
        args={'origin':origin,'destination':destination}
        if mode=='transit':args.update(city=city,cityd=city)
        data=await self._call('maps_direction_'+{'walking':'walking','driving':'driving','transit':'transit_integrated'}[mode],args)
        route=data.get('route',data)
        paths=route.get('transits' if mode=='transit' else 'paths',[])
        if not paths:raise ToolError('NO_ROUTE','高德 MCP 未查到路线')
        p=paths[0]
        try:
            seconds=float(p.get('duration') or p.get('cost',{}).get('duration'))
            distance=float(p.get('distance') or route.get('distance'))
        except (TypeError,ValueError):raise ToolError('INVALID_OUTPUT','高德 MCP 路线缺少耗时或距离')
        return {'minutes':math.ceil(seconds/60),'km':round(distance/1000,2),'source':Source(provider='高德远程 MCP 路线',status='observed',detail='查询估算耗时，实际出行需留缓冲').model_dump()}
    async def weather(self,city):
        if not self.remote_url:return await self._call('weather',{'city':city})
        data=await self._call('maps_weather',{'city':city})
        forecasts=data.get('forecasts',[])
        if forecasts and 'casts' in forecasts[0]:forecasts=[p for f in forecasts for p in f.get('casts',[])]
        if not forecasts:raise ToolError('NO_RESULTS','高德 MCP 未查到天气预报')
        return {'forecasts':[{'date':p['date'],'day_weather':p.get('dayweather',p.get('day_weather','未知'))} for p in forecasts],'source':Source(provider='高德远程 MCP 天气',status='observed').model_dump()}
