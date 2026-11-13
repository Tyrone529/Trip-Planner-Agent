from datetime import date,timedelta
from .models import PlanRequest,Issue

def minute(t):h,m=map(int,t.split(':'));return h*60+m
def clock(m):return f'{m//60:02d}:{m%60:02d}'
def budget(days):
    known=0;unknown=0;estimate=0;rows=[]
    for i,day in enumerate(days):
        values=[(a['poi']['name'],a['poi'].get('price'),a['poi'].get('price_status','unknown')) for a in day['activities']]
        if day.get('hotel') and i<len(days)-1:values.append((day['hotel']['name'],day['hotel'].get('price'),day['hotel'].get('price_status','unknown')))
        values.append(('餐饮预算',day.get('meal_budget',100),'estimated'))
        values.append(('交通费用',None,'unknown'))
        for name,value,status in values:
            if value is None:unknown+=1
            elif status=='observed':known+=value
            else:estimate+=value
            rows.append({'day_index':i,'name':name,'amount':value,'status':status})
    return {'known_total':known,'estimated_total':estimate,'subtotal':round(known+estimate,2),'unknown_items':unknown,'rows':rows,'verified':unknown==0 and estimate==0}

def validate(plan,request:PlanRequest,previous=None,affected=None):
    issues=[];days=plan['days'];c=request.constraints
    def add(code,msg,day=None,severity='error'):issues.append(Issue(code=code,severity=severity,message=msg,day_index=day).model_dump())
    if len(days)!=request.travel_days:add('DAY_COUNT','行程天数不一致')
    names=set()
    for i,day in enumerate(days):
        if day['date']!=(request.start_date+timedelta(days=i)).isoformat():add('DATE','行程日期不连续',i)
        end=minute(c.day_start);walk=0
        if not day['activities']:add('EMPTY_DAY','没有可用活动，请调整筛选条件',i)
        for a in day['activities']:
            names.add(a['poi']['name'])
            start=minute(a['start']);finish=minute(a['end']);leg=day.get('route_checks',{}).get(a['id'],{}).get('minutes',a.get('leg_minutes'))
            if finish<=start:add('DURATION','活动结束时间必须晚于开始时间',i)
            if start<end+(leg or 0):add('TIME_CONFLICT','活动或交通时间发生冲突',i)
            if finish>minute(day.get('deadline') or c.day_end):add('DEADLINE','活动超过当日截止时间',i)
            if leg is None:add('ROUTE_UNKNOWN','交通时间未查询成功，不能确认时间可行性',i,'warning')
            if request.transportation=='步行':walk+=day.get('route_checks',{}).get(a['id'],{}).get('km',a.get('leg_km')) or 0
            end=finish
        if c.max_walk_km and request.transportation!='步行':add('WALK_UNKNOWN','混合或公共交通路线暂未校验全部步行距离',i,'warning')
        if c.max_walk_km and request.transportation=='步行' and walk>c.max_walk_km:add('WALK_LIMIT','估算步行距离超过上限',i)
        if not day.get('hotel'):add('HOTEL_UNKNOWN','尚未查到住宿候选',i,'warning')
    for name in c.must_visit:
        if not any(name in n for n in names):add('MUST_VISIT','未满足必去地点：'+name)
    b=budget(days)
    if c.max_budget and b['subtotal']>c.max_budget:add('BUDGET','已知费用与估算费用小计超过预算上限')
    if c.max_budget and not b['verified']:add('PRICE_UNKNOWN','部分费用为估算或未知，不能确认最终预算',severity='warning')
    if previous:
        old={d['day_index']:d for d in previous['days']}
        for d in days:
            o=old[d['day_index']]
            if affected is not None and d['day_index']!=affected and d!=o:add('UNAFFECTED_CHANGED','非目标日期被修改',d['day_index'])
            if o.get('hotel_locked') and d.get('hotel')!=o.get('hotel'):add('HOTEL_LOCK','已锁定住宿被修改',d['day_index'])
            current={a['id']:a for a in d['activities']}
            for a in o['activities']:
                if a.get('locked') and current.get(a['id'])!=a:add('ACTIVITY_LOCK','已锁定活动被修改',d['day_index'])
    return issues,b
