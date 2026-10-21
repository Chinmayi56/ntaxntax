import { useEffect, useMemo, useState } from "react";
import { Clock, CheckCircle2, ClipboardList, Building2, FolderKanban, PlaneTakeoff, Bell, TrendingUp, LogIn, AlertCircle } from "lucide-react";
import PageHeader from "@/components/shared/PageHeader";
import { KpiCard } from "@/components/shared/KpiCard";
import { ChartCard, BarChartView, DonutChartView } from "@/components/shared/Charts";
import { ActivityFeed, DueDatesWidget } from "@/components/shared/Widgets";
import { useAuth } from "@/context/AuthContext";
import api, { describeApiError } from "@/lib/api";
import { toast } from "sonner";

const B="/employee";
export default function EmployeeDashboard(){
 const {user}=useAuth(); const [loading,setLoading]=useState(true); const [summary,setSummary]=useState(null); const [attendance,setAttendance]=useState([]); const [leaves,setLeaves]=useState([]); const [notifications,setNotifications]=useState({notifications:[],unread:0}); const [error,setError]=useState("");
 const load=async()=>{setLoading(true);try{const [s,a,l,n]=await Promise.all([api.get("/employee/workspace/summary"),api.get("/attendance"),api.get("/leaves"),api.get("/notifications")]);setSummary(s.data?.data||null);setAttendance(a.data?.data||[]);setLeaves(l.data?.data||[]);setNotifications(n.data?.data||{notifications:[],unread:0});setError("")}catch(e){setError(describeApiError(e,"Unable to load Employee workspace."));toast.error(describeApiError(e,"Unable to load Employee workspace."));}finally{setLoading(false)}};
 useEffect(()=>{load();const h=()=>load();window.addEventListener("ntaxco:data-changed",h);const t=setInterval(()=>{if(!document.hidden)load()},15000);return()=>{window.removeEventListener("ntaxco:data-changed",h);clearInterval(t)}},[]);
 const counts=useMemo(()=>summary?.counts||{},[summary]); const today=new Date().toISOString().slice(0,10); const todayAttendance=attendance.find(a=>a.date===today); const pendingLeaves=leaves.filter(x=>x.status==="Pending").length;
 const cards=[
  {title:"Today's Attendance",value:todayAttendance?.status||"Not marked",icon:LogIn,to:`${B}/attendance`},
  {title:"Working Hours Today",value:todayAttendance?.hours&&todayAttendance.hours!=="-"?todayAttendance.hours:"—",icon:Clock,to:`${B}/attendance`},
  {title:"Assigned Customers",value:counts.customers??"—",icon:Building2,to:`${B}/customers`},
  {title:"Tasks Due Today",value:counts.due_today??"—",icon:ClipboardList,to:`${B}/tasks`},
  {title:"Overdue Tasks",value:counts.overdue??"—",icon:AlertCircle,to:`${B}/tasks`},
  {title:"In Progress",value:counts.in_progress??"—",icon:Clock,to:`${B}/tasks`},
  {title:"Waiting for Review",value:counts.review??"—",icon:CheckCircle2,to:`${B}/tasks`},
  {title:"Projects",value:counts.projects??"—",icon:FolderKanban,to:`${B}/projects`},
  {title:"Pending Leave Requests",value:pendingLeaves,icon:PlaneTakeoff,to:`${B}/leave`},
  {title:"Notifications",value:notifications.unread??0,icon:Bell,to:`${B}/notifications`},
  {title:"GST Records",value:counts.gst??0,icon:TrendingUp,to:`${B}/gst`},
  {title:"Income Tax Records",value:counts.itr??0,icon:TrendingUp,to:`${B}/income-tax`},
  {title:"TDS Records",value:counts.tds??0,icon:TrendingUp,to:`${B}/tds`},
  {title:"ROC Records",value:counts.roc??0,icon:TrendingUp,to:`${B}/roc`},
 ];
 const attendanceBreakdown=useMemo(()=>{const c={};attendance.forEach(a=>c[a.status]=(c[a.status]||0)+1);return Object.entries(c).map(([name,value])=>({name,value}))},[attendance]);
 const taskStatusChart=useMemo(()=>[{status:"TO DO",count:Math.max(0,(counts.tasks||0)-(counts.in_progress||0)-(counts.review||0)-(counts.completed_tasks||0))},{status:"IN PROGRESS",count:counts.in_progress||0},{status:"REVIEW",count:counts.review||0},{status:"COMPLETED",count:counts.completed_tasks||0}],[counts]);
 const activityItems=useMemo(()=> (summary?.recent_activity||[]).slice(0,6).map(a=>({title:a.text||a.action||"Activity",time:a.created_at?new Date(a.created_at).toLocaleString("en-IN"):"",tag:"Task"})),[summary]);
 const dueItems=useMemo(()=> (summary?.deadlines||[]).slice(0,8).map(d=>({title:d.title||"Task",date:d.due_date,type:d.priority||"Deadline"})),[summary]);
 return <div><PageHeader title="Employee Dashboard" subtitle={`Welcome back, ${user?.name||"Employee"}${summary?.employee?.designation?` — ${summary.employee.designation}`:""}.`} breadcrumb={["Employee","Dashboard"]}/>{error&&<div className="mb-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>}<div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 mb-6">{cards.map((c,i)=><KpiCard key={i}{...c}loading={loading}testId={`employee-kpi-${i}`}/>)}</div><div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6"><ChartCard title="Attendance Breakdown">{attendanceBreakdown.length?<DonutChartView data={attendanceBreakdown}/>:<p className="text-sm text-muted-foreground">No attendance records yet.</p>}</ChartCard><ChartCard title="Task Status">{(counts.tasks||0)>0?<BarChartView data={taskStatusChart} xKey="status" keys={[{key:"count",name:"Tasks"}]}/>:<p className="text-sm text-muted-foreground">No tasks assigned.</p>}</ChartCard></div><div className="grid grid-cols-1 lg:grid-cols-2 gap-6"><ActivityFeed items={activityItems}/><DueDatesWidget items={dueItems}/></div></div>;
}
