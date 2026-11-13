import asyncio, copy, hashlib, json, time
from datetime import datetime,timezone
from typing import Callable
from pydantic import BaseModel,Field,ValidationError
from .models import utcnow

class ToolError(Exception):
    def __init__(self,code,message,retryable=False):
        self.code=code;self.message=message;self.retryable=retryable;super().__init__(message)

class SearchArgs(BaseModel):
    city:str=Field(min_length=1,max_length=60)
    keywords:str=Field(min_length=1,max_length=100)
    kind:str=Field(default='attraction',pattern='^(hotel|attraction)$')

class RouteArgs(BaseModel):
    origin:str=Field(pattern=r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$')
    destination:str=Field(pattern=r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$')
    city:str
    mode:str=Field(pattern='^(walking|driving|transit)$')

class WeatherArgs(BaseModel): city:str=Field(min_length=1,max_length=60)

class ToolExecutor:
    """Validated read-tool execution with TTL cache and bounded transient retries."""
    def __init__(self,timeout=12,retries=2,ttl=300):
        self.timeout=timeout;self.retries=retries;self.ttl=ttl;self.cache={};self.locks={}
    async def call(self,name,args,schema,fn:Callable,trace):
        start=time.monotonic()
        try:parsed=schema.model_validate(args).model_dump()
        except ValidationError as e:
            trace.append({'tool':name,'status':'invalid_arguments','attempts':0,'elapsed_ms':0})
            raise ToolError('INVALID_ARGUMENTS','工具参数不合法') from e
        key=hashlib.sha256(json.dumps([name,parsed],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        lock=self.locks.setdefault(key,asyncio.Lock())
        async with lock:
            cached=self.cache.get(key)
            if cached and time.monotonic()-cached[0]<self.ttl:
                trace.append({'tool':name,'arguments':parsed,'status':'cache_hit','attempts':0,'elapsed_ms':round((time.monotonic()-start)*1000)})
                return copy.deepcopy(cached[1])
            for attempt in range(self.retries+1):
                try:
                    result=await asyncio.wait_for(fn(**parsed),timeout=self.timeout)
                    if result is None or result==[]:raise ToolError('NO_RESULTS','查询未找到结果')
                    self.cache[key]=(time.monotonic(),copy.deepcopy(result))
                    trace.append({'tool':name,'arguments':parsed,'status':'ok','attempts':attempt+1,'elapsed_ms':round((time.monotonic()-start)*1000),'queried_at':utcnow()})
                    return result
                except asyncio.TimeoutError:err=ToolError('TIMEOUT','外部工具响应超时',True)
                except ToolError as e:err=e
                except Exception:err=ToolError('TOOL_FAILURE','外部工具执行失败，请检查服务配置')
                if not err.retryable or attempt==self.retries:
                    trace.append({'tool':name,'arguments':parsed,'status':'error','code':err.code,'attempts':attempt+1,'elapsed_ms':round((time.monotonic()-start)*1000)})
                    raise err
                await asyncio.sleep(.15*(2**attempt))
