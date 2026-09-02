#!/usr/bin/env python3
"""직원 인사 레코드 시드 — EMP 아이템은 공개 쓰기 API가 없다(HR 시스템 소관).
데모에서는 이 스크립트로 직접 적재한다. 실서비스에서는 HR 마스터에서 동기화한다."""
import boto3
from decimal import Decimal
t = boto3.resource("dynamodb", region_name="ap-northeast-2").Table("agentic-book-demo-registry")
def D(o):
    if isinstance(o,dict): return {k:D(v) for k,v in o.items()}
    if isinstance(o,list): return [D(v) for v in o]
    if isinstance(o,(int,float)): return Decimal(str(o))
    return o
EMPS = [
  {"pk":"EMP","sk":"demo@atomai.click","name":"김데모","empNo":"H2024-0007","dept":"리테일플랫폼팀","rank":"선임","joinDate":"2021-03-02","years":4,
   "leave":{"granted":16,"used":9.5,"pending":1,"history":[
     {"date":"2026-07-28","type":"연차","days":2,"status":"완료"},
     {"date":"2026-08-14","type":"반차","days":0.5,"status":"완료"},
     {"date":"2026-08-25","type":"연차","days":3,"status":"완료"},
     {"date":"2026-09-15","type":"연차","days":1,"status":"신청중"}]}},
  {"pk":"EMP","sk":"alpha@demo.nexus","name":"이알파","empNo":"H2025-0031","dept":"리서치센터","rank":"사원","joinDate":"2025-01-06","years":1,
   "leave":{"granted":15,"used":3,"pending":0,"history":[
     {"date":"2026-05-02","type":"연차","days":1,"status":"완료"},
     {"date":"2026-06-19","type":"연차","days":2,"status":"완료"}]}},
  {"pk":"EMP","sk":"beta@demo.nexus","name":"박베타","empNo":"H2019-0114","dept":"준법감시팀","rank":"책임","joinDate":"2018-09-03","years":7,
   "leave":{"granted":19,"used":17,"pending":2,"history":[
     {"date":"2026-08-01","type":"연차","days":5,"status":"완료"},
     {"date":"2026-08-18","type":"연차","days":4,"status":"완료"},
     {"date":"2026-09-22","type":"연차","days":2,"status":"신청중"}]}},
]
for e in EMPS:
    t.put_item(Item=D(e)); print("EMP seeded:", e["sk"])
