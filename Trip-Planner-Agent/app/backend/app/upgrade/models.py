from datetime import date, datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, model_validator

def utcnow(): return datetime.now(timezone.utc).isoformat()

class Constraints(BaseModel):
    max_budget: float | None = Field(default=None, gt=0)
    day_start: str = Field(default='09:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    day_end: str = Field(default='18:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    must_visit: list[str] = Field(default_factory=list,max_length=10)
    max_walk_km: float | None = Field(default=None,gt=0)
    @model_validator(mode='after')
    def valid_window(self):
        if self.day_start >= self.day_end: raise ValueError('每日开始时间必须早于结束时间')
        return self

class PlanRequest(BaseModel):
    city: str = Field(default='北京',min_length=1,max_length=60)
    start_date: date
    end_date: date
    travel_days: int = Field(default=3,ge=1,le=7)
    transportation: Literal['公共交通','步行','自驾','混合'] = '公共交通'
    accommodation: str = '经济型酒店'
    preferences: list[str] = Field(default_factory=lambda:['历史文化'])
    free_text_input: str = Field(default='',max_length=1000)
    constraints: Constraints = Field(default_factory=Constraints)
    mode: Literal['replay','live','mcp'] = 'replay'
    use_llm: bool = True
    @model_validator(mode='after')
    def dates_match(self):
        if (self.end_date-self.start_date).days+1 != self.travel_days:
            raise ValueError('开始日期、结束日期和旅行天数必须一致')
        return self

class Source(BaseModel):
    provider: str
    queried_at: str = Field(default_factory=utcnow)
    status: Literal['observed','estimated','fixture','unknown']
    detail: str = ''

class Place(BaseModel):
    id: str
    name: str
    address: str
    longitude: float = Field(ge=-180,le=180)
    latitude: float = Field(ge=-90,le=90)
    indoor: bool | None = None
    kind: Literal['attraction','hotel'] = 'attraction'
    duration_min: int = Field(default=90,gt=0,le=480)
    price: float | None = Field(default=None,ge=0)
    price_status: Literal['estimated','unknown','fixture'] = 'unknown'
    source: Source

class Activity(BaseModel):
    id: str
    poi: Place
    start: str
    end: str
    locked: bool = False
    leg_minutes: int | None = None
    leg_km: float | None = None
    route_source: Source | None = None

class Day(BaseModel):
    date: str
    day_index: int
    activities: list[Activity]
    hotel: Place | None = None
    hotel_locked: bool = False
    deadline: str | None = None
    route_checks: dict[str,dict] = Field(default_factory=dict)
    meal_budget: float = 100
    notes: list[str] = Field(default_factory=list)

class Issue(BaseModel):
    code: str
    severity: Literal['error','warning']
    message: str
    day_index: int | None = None

class PatchRequest(BaseModel):
    expected_version: int = Field(ge=1)
    day_index: int = Field(ge=0,le=6)
    action: Literal['indoor','relaxed','custom','constraints'] = 'indoor'
    instruction: str = Field(default='',max_length=1000)
    day_end: str | None = Field(default=None,pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    constraints: Constraints | None = None
    @model_validator(mode='after')
    def custom_has_instruction(self):
        if self.action=='custom' and not self.instruction.strip(): raise ValueError('请输入修改要求')
        return self

class LockRequest(BaseModel):
    expected_version: int = Field(ge=1)
    day_index: int = Field(ge=0,le=6)
    activity_id: str | None = None
    locked: bool = True
