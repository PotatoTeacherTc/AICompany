import React from "react";
import { ActivityCharacter, CEOWelcome, CompanySummaryCards, CurrentWorkList, DepartmentPanel, EmployeeCard, FutureFeaturePanel, type DisplayJob } from "./company";

export function HomePage({companyName,jobs,onOpen}:{companyName:string;jobs:DisplayJob[];onOpen:(job:DisplayJob)=>void}){
 const current=jobs.find(job=>job.status==="RUNNING")||jobs[0];
 return <div className="company-home"><CEOWelcome companyName={companyName} currentJob={current}/><CompanySummaryCards jobs={jobs}/><div className="home-columns"><CurrentWorkList jobs={jobs} onOpen={onOpen}/><ActivityCharacter stage={current?.current_stage}/></div><DepartmentPanel name="Potato Company 조직" description="직원과 부서 실행 기능은 아직 연결되지 않았습니다." activeWork={0} employees={[<EmployeeCard key="general" characterId="general-potato" employeeName="General Potato" departmentName="운영" roleName="AI 직원 계약"/>]}/><section className="future-grid" aria-label="향후 기능"><FutureFeaturePanel title="Research" description="Research 데이터 연결 준비 중"/><FutureFeaturePanel title="Meeting" description="회의 기능 연결 준비 중"/><FutureFeaturePanel title="Bible" description="콘텐츠 Bible 연결 준비 중"/><FutureFeaturePanel title="Announcement" description="공지 기능 연결 준비 중"/></section></div>
}
