"""30 deterministic replay cases; repair ablation, NOT original live-LLM comparison."""
import sys,asyncio,tempfile,json,time,copy
from pathlib import Path
from datetime import date,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.upgrade.models import PlanRequest,Constraints,PatchRequest
from app.upgrade.store import Store
from app.upgrade.engine import Planner
OUT=Path(__file__).resolve().parents[3]/'evaluation-results'
async def execute(p,req,old=None,patch=None):
 r=p.start(req,old,patch)
 while (x:=p.store.get_run(r['id']))['status']=='running':await asyncio.sleep(.001)
 if x['status']!='completed':raise AssertionError(x.get('error'))
 return x,p.store.get(x['plan_id'])
def req(days=3,**kwargs):return PlanRequest(start_date='2026-11-05',end_date=(date(2026,11,5)+timedelta(days=days-1)).isoformat(),travel_days=days,**kwargs)
async def main():
 cases=[];started=time.monotonic()
 with tempfile.TemporaryDirectory() as directory:
  p=Planner(Store(Path(directory)/'eval.db'))
  for days in [1,2,3,4]:
   for end in ['18:00','15:00','12:00','11:30']:
    run,plan=await execute(p,req(days,constraints=Constraints(day_end=end)))
    before=sum(i['severity']=='error' for i in run.get('validation_before_repair',[]));after=sum(i['severity']=='error' for i in plan['validation'])
    cases.append({'id':f'plan-{days}-{end}','kind':'feasible_plan','pass':after==0,'before_errors':before,'after_errors':after,'elapsed_ms':run['elapsed_ms'],'tool_attempts':sum(t['attempts'] for t in run['trace'])})
  for days in [1,2,3,4]:
   run,plan=await execute(p,req(days,constraints=Constraints(max_budget=1)))
   cases.append({'id':f'budget-{days}','kind':'infeasible_budget','pass':plan['status']=='conflicts' and any(i['code']=='BUDGET' for i in plan['validation']),'elapsed_ms':run['elapsed_ms']})
  for n in range(10):
   _,old=await execute(p,req());old['days'][1]['hotel_locked']=True;old=p.store.save(old,old['version']);previous=copy.deepcopy(old)
   patch=PatchRequest(expected_version=old['version'],day_index=1,action='indoor' if n%2 else 'relaxed')
   run,new=await execute(p,req(),old,patch)
   passed=new['days'][0]==previous['days'][0] and new['days'][2]==previous['days'][2] and new['days'][1]['hotel']==previous['days'][1]['hotel']
   cases.append({'id':f'revision-{n}','kind':'local_revision','pass':passed,'elapsed_ms':run['elapsed_ms']})
 summary={'case_count':len(cases),'passed':sum(c['pass'] for c in cases),'mode':'deterministic_replay','llm_calls':0,'external_map_calls':0,'comparison':'Same generated draft before versus after rule-based repair; NOT an upstream live baseline','before_repair_errors':sum(c.get('before_errors',0) for c in cases),'after_repair_errors_on_feasible_cases':sum(c.get('after_errors',0) for c in cases),'duration_seconds':round(time.monotonic()-started,3)}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'replay-evaluation.json').write_text(json.dumps({'summary':summary,'cases':cases},ensure_ascii=False,indent=2))
 lines=['# 离线评测报告','',f"固定回放案例：{summary['case_count']}；通过：{summary['passed']}。",'',f"可行案例中，修正前草案硬约束错误共 {summary['before_repair_errors']} 项，修正后 {summary['after_repair_errors_on_feasible_cases']} 项。",'', '范围：16个可行规划、4个预算不可行案例、10个带住宿锁定的局部修改案例。','', '离线快照不是真实地图数据；本评测未调用LLM，不代表真实旅行质量或模型准确率。对照仅为同一流程的修正前后消融，不是原版与改版的线上效果比较。','', '| 案例 | 类别 | 通过 |','| --- | --- | --- |']
 lines += [f"| {c['id']} | {c['kind']} | {'是' if c['pass'] else '否'} |" for c in cases]
 (OUT/'离线评测报告.md').write_text('\n'.join(lines)+'\n');print(json.dumps(summary,ensure_ascii=False))
 if not all(c['pass'] for c in cases):raise SystemExit(1)
asyncio.run(main())
