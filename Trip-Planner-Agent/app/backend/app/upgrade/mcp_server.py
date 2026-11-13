"""Typed MCP tools around the same validated Amap adapter. No LLM credentials passed."""
import json,os,logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from .tools import ToolError
from mcp.server.fastmcp import FastMCP
from .providers import AmapProvider,ReplayProvider
mcp=FastMCP('trip-map-tools')
provider=ReplayProvider() if os.getenv('TRIP_MCP_FIXTURE')=='1' else AmapProvider()
async def safe(coro):
    try:return json.dumps(await coro,ensure_ascii=False)
    except ToolError as e:return json.dumps({'error':{'code':e.code,'message':e.message,'retryable':e.retryable}},ensure_ascii=False)
@mcp.tool()
async def search_poi(city:str,keywords:str,kind:str='attraction')->str:
    return await safe(provider.search(city,keywords,kind))
@mcp.tool()
async def weather(city:str)->str:return await safe(provider.weather(city))
@mcp.tool()
async def route(origin:str,destination:str,city:str,mode:str)->str:
    return await safe(provider.route(origin,destination,city,mode))
if __name__=='__main__':mcp.run(transport='stdio')
