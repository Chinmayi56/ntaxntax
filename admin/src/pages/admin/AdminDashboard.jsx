import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Users, Building2, UserCog, Wallet, Receipt, CalendarCheck, AlertCircle, RefreshCw,
  IndianRupee, CheckCircle2, Clock3, FileWarning, ListTodo, CreditCard,
  FileUp, Bell, UserRoundPlus, BriefcaseBusiness, Filter
} from "lucide-react";
import PageHeader from "@/components/shared/PageHeader";
import { KpiCard } from "@/components/shared/KpiCard";
import { ChartCard, AreaChartView, BarChartView, LineChartView, DonutChartView } from "@/components/shared/Charts";
import { ActivityFeed, DueDatesWidget } from "@/components/shared/Widgets";
import RemindersWidget from "@/components/shared/RemindersWidget";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import api, { describeApiError } from "@/lib/api";
import { toast } from "sonner";
import { inr } from "@/lib/utils";

const B = "/admin";

function dateString(date) {
  return date.toISOString().slice(0, 10);
}

function presetRange(preset) {
  const now = new Date();
  const today = dateString(now);
  if (preset === "today") return [today, today];
  const day = now.getDay();
  const mondayOffset = day === 0 ? 6 : day - 1;
  if (preset === "week") {
    const start = new Date(now); start.setDate(now.getDate() - mondayOffset);
    return [dateString(start), today];
  }
  if (preset === "month") {
    return [`${today.slice(0, 7)}-01`, today];
  }
  if (preset === "quarter") {
    const quarterMonth = Math.floor(now.getMonth() / 3) * 3;
    const start = new Date(now.getFullYear(), quarterMonth, 1);
    return [dateString(start), today];
  }
  if (preset === "year") return [`${now.getFullYear()}-01-01`, today];
  return ["", ""];
}

function Section({ title, subtitle, children, className = "" }) {
  return (
    <section className={`bg-white border border-zinc-200 rounded-xl shadow-sm p-4 sm:p-5 ${className}`}>
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h2 className="font-heading text-base font-semibold text-zinc-900">{title}</h2>
          {subtitle && <p className="text-xs text-zinc-500 mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

function MetricTile({ label, value, icon: Icon, onClick, tone = "royal" }) {
  const tones = {
    royal: "bg-royal-faint text-royal",
    green: "bg-emerald-50 text-emerald-600",
    amber: "bg-amber-50 text-amber-600",
    red: "bg-red-50 text-red-600",
    purple: "bg-purple-50 text-purple-600",
    teal: "bg-teal-50 text-teal-600",
  };
  return (
    <button
      onClick={onClick}
      className="text-left rounded-lg border border-zinc-100 bg-zinc-50 p-3 hover:bg-white hover:border-zinc-300 transition-colors"
    >
      <div className={`h-8 w-8 rounded-lg flex items-center justify-center ${tones[tone] || tones.royal}`}>
        <Icon className="h-4 w-4" />
      </div>
      <p className="text-xl font-semibold text-zinc-900 mt-2">{value}</p>
      <p className="text-xs text-zinc-500 mt-0.5">{label}</p>
    </button>
  );
}

function ErrorBox({ message, onRetry }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      <div className="flex items-center justify-between gap-3">
        <span>{message}</span>
        {onRetry && <Button size="sm" variant="outline" onClick={onRetry}>Retry</Button>}
      </div>
    </div>
  );
}

export default function AdminDashboard() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [taskSummary, setTaskSummary] = useState(null);
  const [taskAnalytics, setTaskAnalytics] = useState(null);
  const [revSummary, setRevSummary] = useState(null);
  const [primeCustomerCount, setPrimeCustomerCount] = useState(null);
  const [errors, setErrors] = useState({});
  const [preset, setPreset] = useState("all");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  const applyPreset = (value) => {
    setPreset(value);
    if (value === "custom") return;
    const [start, end] = presetRange(value);
    setStartDate(start);
    setEndDate(end);
  };

  const load = useCallback(async () => {
    setLoading(true);
    setErrors({});
    const params = {};
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;

    const results = await Promise.allSettled([
      api.get("/admin/prime-customers/stats").then(({ data }) => setPrimeCustomerCount(data?.total_customers ?? 0)),
      api.get("/admin/dashboard", { params }),
      api.get("/admin/analytics/summary"),
      api.get("/admin/tasks/summary"),
      api.get("/admin/tasks/analytics"),
      api.get("/admin/dashboard/revenue-summary"),
    ]);

    const [primeStatsResult, dashboardResult, analyticsResult, taskResult, taskAnalyticsResult, revenueResult] = results;
    if (primeStatsResult.status !== "fulfilled") setPrimeCustomerCount(null);
    const nextErrors = {};
    if (dashboardResult.status === "fulfilled") setSummary(dashboardResult.value.data?.data || null);
    else nextErrors.dashboard = describeApiError(dashboardResult.reason, "Unable to load dashboard data.");
    if (analyticsResult.status === "fulfilled") setAnalytics(analyticsResult.value.data?.data || null);
    else nextErrors.analytics = describeApiError(analyticsResult.reason, "Unable to load financial analytics.");
    if (taskResult.status === "fulfilled") setTaskSummary(taskResult.value.data?.data || null);
    else nextErrors.tasks = describeApiError(taskResult.reason, "Unable to load task summary.");
    if (taskAnalyticsResult.status === "fulfilled") setTaskAnalytics(taskAnalyticsResult.value.data?.data || null);
    else nextErrors.taskAnalytics = describeApiError(taskAnalyticsResult.reason, "Unable to load task analytics.");
    if (revenueResult.status === "fulfilled") setRevSummary(revenueResult.value.data?.data || null);
    else nextErrors.revenue = describeApiError(revenueResult.reason, "Unable to load revenue summary.");
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) toast.error("Some dashboard sections could not be refreshed. Other sections remain available.");
    setLoading(false);
  }, [startDate, endDate]);

  useEffect(() => {
    load();
    const onChange = (event) => {
      const resources = event?.detail?.resources || [];
      if (resources.some((r) => ["customers", "bookings", "invoices", "payments", "agents", "services", "tasks", "documents"].includes(r))) load();
    };
    window.addEventListener("ntaxco:data-changed", onChange);
    const interval = window.setInterval(load, 60000);
    return () => {
      window.removeEventListener("ntaxco:data-changed", onChange);
      window.clearInterval(interval);
    };
    // Dashboard refreshes when the selected date range changes.
  }, [load]);

  const c = useMemo(() => summary?.cards || {}, [summary]);
  const charts = summary?.charts || {};
  const fin = summary?.financial_health;
  const attention = summary?.attention_required || [];
  const operations = summary?.today_operations;
  const compliance = summary?.compliance_overview || [];
  const customerHealth = summary?.customer_health;
  const servicePerformance = summary?.service_performance || [];
  const bookingFunnel = summary?.booking_funnel || [];
  const deadlines = summary?.deadlines || {};
  const activity = summary?.activity_feed || [];
  const workload = summary?.employee_workload || [];
  const dashboardTaskSummary = summary?.task_summary;
  const dashboardTaskAnalytics = summary?.task_analytics;
  const effectiveTaskSummary = dashboardTaskSummary || taskSummary;
  const effectiveTaskAnalytics = dashboardTaskAnalytics || taskAnalytics;
  const serviceCounts = analytics?.service_counts || {};
  const paymentCounts = analytics?.payment_status_counts || {};
  const bookingCounts = analytics?.booking_status_counts || {};

  const cards = useMemo(() => [
    { title: "Total Customers", value: c.customers ?? "Unavailable", icon: Building2, to: `${B}/customers` },
    { title: "Active Agents", value: c.active_agents ?? "Unavailable", icon: UserCog, to: `${B}/agents` },
    { title: "Revenue Collected", value: fin ? inr(fin.total_paid) : (analytics ? inr(analytics.invoice_financials?.paid || 0) : "Unavailable"), icon: Wallet, to: `${B}/revenue` },
    { title: "Outstanding", value: fin ? inr(fin.outstanding) : (analytics ? inr(analytics.invoice_financials?.balance || 0) : "Unavailable"), icon: AlertCircle, to: `${B}/payments` },
    { title: "Invoices", value: fin ? inr(fin.total_billed) : (analytics ? inr(analytics.invoice_financials?.invoiced || 0) : "Unavailable"), icon: Receipt, to: `${B}/invoices` },
    { title: "Collection Rate", value: fin ? `${fin.collection_rate}%` : (analytics ? `${analytics.collection_rate || 0}%` : "Unavailable"), icon: IndianRupee, to: `${B}/payments` },
    { title: "Bookings", value: c.bookings ?? "Unavailable", icon: CalendarCheck, to: `${B}/bookings` },
    { title: "Employees", value: c.employees ?? "Unavailable", icon: Users, to: `${B}/employees` },
    { title: "Prime Service Customers", value: primeCustomerCount ?? "Unavailable", icon: UserRoundPlus, to: `${B}/prime-customers` },
  ], [c, fin, analytics, primeCustomerCount]);

  const revenueCards = [
    { title: "Employee Revenue", value: errors.revenue ? "Unavailable" : inr(revSummary?.employee?.total_revenue || 0), icon: Users, tone: "purple", to: `${B}/employee-revenue` },
    { title: "Customer Revenue", value: errors.revenue ? "Unavailable" : inr(revSummary?.customer?.total_revenue || 0), icon: Building2, tone: "green", to: `${B}/customer-revenue` },
    { title: "Agent Revenue", value: errors.revenue ? "Unavailable" : inr(revSummary?.agent?.total_revenue || 0), icon: UserCog, tone: "orange", to: `${B}/agent-revenue` },
  ];

  const statusData = Object.entries(bookingCounts).map(([name, value]) => ({ name, value }));
  const serviceData = Object.entries(serviceCounts).map(([name, value]) => ({ name, value }));
  const paymentData = Object.entries(paymentCounts).map(([name, value]) => ({ name, value }));

  return (
    <div>
      <PageHeader
        title="Super Admin Dashboard"
        subtitle="Overview of NTAXCO operations, revenue and compliance."
        breadcrumb={["Super Admin", "Dashboard"]}
        actions={
          <Button variant="outline" className="border-zinc-300" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />Refresh
          </Button>
        }
      />

      <div className="bg-white border border-zinc-200 rounded-xl shadow-sm p-4 mb-6">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex items-center gap-2 mr-1"><Filter className="h-4 w-4 text-zinc-500" /><span className="text-sm font-medium text-zinc-800">Dashboard period</span></div>
          <select value={preset} onChange={(e) => applyPreset(e.target.value)} className="h-9 rounded-md border border-zinc-300 bg-white px-3 text-sm">
            <option value="all">All Time</option>
            <option value="today">Today</option>
            <option value="week">This Week</option>
            <option value="month">This Month</option>
            <option value="quarter">This Quarter</option>
            <option value="year">This Year</option>
            <option value="custom">Custom Range</option>
          </select>
          {(preset === "custom" || startDate || endDate) && (
            <>
              <div><label className="block text-[11px] text-zinc-500 mb-1">From</label><Input type="date" value={startDate} onChange={(e) => { setPreset("custom"); setStartDate(e.target.value); }} /></div>
              <div><label className="block text-[11px] text-zinc-500 mb-1">To</label><Input type="date" value={endDate} onChange={(e) => { setPreset("custom"); setEndDate(e.target.value); }} /></div>
            </>
          )}
          <Button variant="ghost" size="sm" onClick={() => applyPreset("all")}>Clear</Button>
        </div>
        {summary?.date_filter?.start_date && <p className="text-xs text-zinc-500 mt-2">Analytics filtered from {summary.date_filter.start_date} to {summary.date_filter.end_date}.</p>}
      </div>

      {errors.dashboard && <div className="mb-6"><ErrorBox message={errors.dashboard} onRetry={load} /></div>}

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 mb-6">
        {cards.map((x, i) => <KpiCard key={i} {...x} loading={loading && !summary} testId={`kpi-${i}`} />)}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6" data-testid="revenue-cards">
        {revenueCards.map((x, i) => <KpiCard key={x.title} {...x} loading={loading && !revSummary && !errors.revenue} testId={`kpi-revenue-${i}`} />)}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <Section title="Attention Required" subtitle="Live records that need Admin action">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {attention.map((item) => (
              <MetricTile
                key={item.key}
                label={item.label}
                value={item.count}
                icon={item.key.includes("task") ? ListTodo : item.key.includes("payment") || item.key.includes("invoice") ? CreditCard : FileWarning}
                tone={item.count > 0 ? "red" : "green"}
                onClick={() => navigate(item.to)}
              />
            ))}
          </div>
          {!loading && !attention.length && <p className="text-sm text-zinc-500">No attention metrics are available.</p>}
        </Section>

        <Section title="Today's Operations" subtitle={`Server-side IST date: ${summary?.date_filter?.today || "current date"}`}>
          {operations ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <MetricTile label="New Customers" value={operations.new_customers} icon={UserRoundPlus} onClick={() => navigate(`${B}/customers`)} />
              <MetricTile label="New Bookings" value={operations.new_bookings} icon={CalendarCheck} onClick={() => navigate(`${B}/bookings`)} />
              <MetricTile label="Payments Received" value={inr(operations.payments_received)} icon={Wallet} tone="green" onClick={() => navigate(`${B}/payments`)} />
              <MetricTile label="Invoices Created" value={operations.invoices_created} icon={Receipt} onClick={() => navigate(`${B}/invoices`)} />
              <MetricTile label="Tasks Created" value={operations.tasks_created} icon={ListTodo} onClick={() => navigate(`${B}/task-board`)} />
              <MetricTile label="Tasks Completed" value={operations.tasks_completed} icon={CheckCircle2} tone="green" onClick={() => navigate(`${B}/task-board`)} />
              <MetricTile label="Documents Uploaded" value={operations.documents_uploaded} icon={FileUp} onClick={() => navigate(`${B}/documents`)} />
              <MetricTile label="New Notifications" value={operations.new_notifications} icon={Bell} onClick={() => navigate(`${B}/notifications`)} />
            </div>
          ) : <p className="text-sm text-zinc-500">Today's operational data is unavailable.</p>}
        </Section>
      </div>

      <Section title="Financial Health" subtitle="Invoice-backed and payment-backed totals; no dashboard-only accounting values" className="mb-6">
        {fin ? (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            <MetricTile label="Total Billed" value={inr(fin.total_billed)} icon={Receipt} onClick={() => navigate(`${B}/invoices`)} />
            <MetricTile label="Total Paid" value={inr(fin.total_paid)} icon={Wallet} tone="green" onClick={() => navigate(`${B}/payments`)} />
            <MetricTile label="Outstanding" value={inr(fin.outstanding)} icon={AlertCircle} tone="amber" onClick={() => navigate(`${B}/payments`)} />
            <MetricTile label="Overdue Amount" value={inr(fin.overdue_amount)} icon={Clock3} tone="red" onClick={() => navigate(`${B}/payments`)} />
            <MetricTile label="Paid Invoices" value={fin.paid_invoices} icon={CheckCircle2} tone="green" onClick={() => navigate(`${B}/invoices`)} />
            <MetricTile label="Pending Invoices" value={fin.pending_invoices} icon={Clock3} tone="amber" onClick={() => navigate(`${B}/invoices`)} />
            <MetricTile label="Partially Paid" value={fin.partially_paid_invoices} icon={CreditCard} tone="purple" onClick={() => navigate(`${B}/invoices`)} />
          </div>
        ) : <p className="text-sm text-zinc-500">Financial health is unavailable.</p>}
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <Section title="Compliance Overview" subtitle="GST, Income Tax, TDS and ROC from existing compliance records">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-zinc-500 border-b"><th className="py-2">Compliance</th><th>Pending</th><th>Processing</th><th>Completed</th><th>Overdue</th></tr></thead>
              <tbody>
                {compliance.map((row) => (
                  <tr key={row.key} className="border-b last:border-0 hover:bg-zinc-50 cursor-pointer" onClick={() => navigate(row.to)}>
                    <td className="py-3 font-medium text-zinc-800">{row.label}</td><td>{row.pending}</td><td>{row.processing}</td><td>{row.completed}</td><td className={row.overdue ? "text-red-600 font-semibold" : ""}>{row.overdue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section title="Booking Funnel" subtitle="Presentation buckets map to existing booking statuses">
          <div className="space-y-3">
            {bookingFunnel.map((row, i) => (
              <button key={row.label} onClick={() => navigate(`${B}/bookings`)} className="w-full text-left">
                <div className="flex items-center justify-between text-xs mb-1"><span className="text-zinc-600">{row.label}</span><span className="font-semibold text-zinc-900">{row.count} · {row.percentage}%</span></div>
                <div className="h-2 rounded-full bg-zinc-100 overflow-hidden"><div className="h-full bg-brand rounded-full" style={{ width: `${Math.min(row.percentage, 100)}%` }} /></div>
              </button>
            ))}
          </div>
        </Section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <Section title="Customer Health" subtitle="Derived from real customer, booking, invoice and compliance relationships">
          {customerHealth ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <MetricTile label="Total Customers" value={customerHealth.total} icon={Building2} onClick={() => navigate(`${B}/customers`)} />
              <MetricTile label="New This Month" value={customerHealth.new_this_month} icon={UserRoundPlus} onClick={() => navigate(`${B}/customers`)} />
              <MetricTile label="Active Customers" value={customerHealth.active} icon={CheckCircle2} tone="green" onClick={() => navigate(`${B}/customers`)} />
              <MetricTile label="Inactive Customers" value={customerHealth.inactive} icon={Clock3} onClick={() => navigate(`${B}/customers`)} />
              <MetricTile label="With Outstanding" value={customerHealth.with_outstanding} icon={CreditCard} tone="amber" onClick={() => navigate(`${B}/payments`)} />
              <MetricTile label="Active Services" value={customerHealth.with_active_services} icon={BriefcaseBusiness} onClick={() => navigate(`${B}/services`)} />
              <MetricTile label="Overdue Compliance" value={customerHealth.with_overdue_compliance} icon={FileWarning} tone="red" onClick={() => navigate(`${B}/gst`)} />
              <MetricTile label="Without Employee" value={customerHealth.without_employee} icon={UserCog} tone="purple" onClick={() => navigate(`${B}/customers`)} />
            </div>
          ) : <p className="text-sm text-zinc-500">Customer health is unavailable.</p>}
        </Section>

        <Section title="Employee Workload" subtitle="Existing Task Board workload by assignee">
          <div className="overflow-x-auto max-h-80">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-white"><tr className="text-left text-zinc-500 border-b"><th className="py-2">Employee</th><th>Assigned</th><th>To Do</th><th>In Progress</th><th>Review</th><th>Completed</th><th>Overdue</th><th>Due Today</th></tr></thead>
              <tbody>
                {workload.slice(0, 30).map((row) => (
                  <tr key={row.name} className="border-b last:border-0 hover:bg-zinc-50 cursor-pointer" onClick={() => navigate(`${B}/task-board`)}>
                    <td className="py-2 font-medium text-zinc-800">{row.name}</td><td>{row.assigned}</td><td>{row.todo}</td><td>{row.in_progress}</td><td>{row.review}</td><td>{row.completed}</td><td className={row.overdue ? "text-red-600 font-semibold" : ""}>{row.overdue}</td><td>{row.due_today}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!workload.length && <p className="text-sm text-zinc-500 py-4">No task workload records.</p>}
          </div>
        </Section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <Section title="Deadlines" subtitle="Real due dates grouped by urgency">
          <div className="grid grid-cols-2 gap-3 mb-4">
            {[["Overdue", deadlines.overdue || [], "red"], ["Due Today", deadlines.due_today || [], "amber"], ["Due Soon", deadlines.due_soon || [], "royal"], ["This Week", deadlines.this_week || [], "green"]].map(([label, items, tone]) => (
              <div key={label} className="rounded-lg border border-zinc-100 p-3">
                <div className="flex items-center justify-between mb-2"><span className="text-xs font-medium text-zinc-600">{label}</span><span className={`font-semibold ${tone === "red" ? "text-red-600" : tone === "amber" ? "text-amber-600" : "text-zinc-900"}`}>{items.length}</span></div>
                <div className="space-y-1.5">{items.slice(0, 4).map((item) => <button key={`${item.kind}-${item.id}`} onClick={() => navigate(item.to)} className="w-full text-left text-xs text-zinc-700 truncate hover:text-brand">{item.title} · {item.due_date}</button>)}</div>
                {!items.length && <p className="text-xs text-zinc-400">None</p>}
              </div>
            ))}
          </div>
        </Section>

        <Section title="Activity Feed" subtitle="Existing audit, task activity and notification events">
          <div className="space-y-2 max-h-80 overflow-y-auto">
            {activity.slice(0, 15).map((item, i) => (
              <div key={`${item.kind}-${item.time}-${i}`} className="flex items-start gap-3 rounded-lg border border-zinc-100 p-2.5">
                <div className="h-8 w-8 rounded-lg bg-zinc-100 flex items-center justify-center shrink-0"><Bell className="h-4 w-4 text-zinc-500" /></div>
                <div className="min-w-0"><p className="text-sm font-medium text-zinc-800 truncate">{item.title}</p><p className="text-xs text-zinc-500 truncate">{item.description || item.kind}</p><p className="text-[10px] text-zinc-400 mt-0.5">{item.time || "Recorded"}</p></div>
              </div>
            ))}
            {!activity.length && <p className="text-sm text-zinc-500 py-4">No activity records are available.</p>}
          </div>
        </Section>
      </div>

      <Section title="Service Performance" subtitle="Existing services linked to bookings and invoices" className="mb-6">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-zinc-500 border-b"><th className="py-2">Service</th><th>Customers</th><th>Bookings</th><th>Revenue</th><th>Pending</th><th>Completed</th><th>Outstanding</th><th>Completion</th></tr></thead>
            <tbody>
              {servicePerformance.map((row) => (
                <tr key={row.id} className="border-b last:border-0 hover:bg-zinc-50 cursor-pointer" onClick={() => navigate(`${B}/services`)}>
                  <td className="py-2 font-medium text-zinc-800">{row.name}</td><td>{row.customers}</td><td>{row.bookings}</td><td>{inr(row.revenue)}</td><td>{row.pending}</td><td>{row.completed}</td><td>{inr(row.outstanding)}</td><td>{row.completion_rate}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!servicePerformance.length && <p className="text-sm text-zinc-500 py-4">No service performance records are available.</p>}
        </div>
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <ChartCard title="Task Management Summary" subtitle="Live status from the internal Task Board">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {[["Total Tasks", effectiveTaskSummary?.total], ["To Do", effectiveTaskSummary?.todo], ["In Progress", effectiveTaskSummary?.in_progress], ["Review", effectiveTaskSummary?.review], ["Completed", effectiveTaskSummary?.completed], ["Unassigned", effectiveTaskSummary?.unassigned], ["Overdue", effectiveTaskSummary?.overdue]].map(([label, value]) => (
              <button key={label} onClick={() => navigate(`${B}/task-board`)} className="rounded-lg bg-zinc-50 border border-zinc-100 p-3 text-left hover:bg-white hover:border-zinc-300">
                <p className="text-xs text-zinc-500">{label}</p><p className="text-xl font-semibold text-zinc-900 mt-1">{value ?? "Unavailable"}</p>
              </button>
            ))}
          </div>
        </ChartCard>
        <Section title="Quick Actions" subtitle="Navigate to existing NTAXCO forms and modules">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {[["Add Customer", `${B}/customers`, UserRoundPlus], ["Create Task", `${B}/task-board`, ListTodo], ["Create Invoice", `${B}/invoices`, Receipt], ["Record Payment", `${B}/payments`, Wallet], ["Add Employee", `${B}/employees`, Users], ["Create Booking", `${B}/bookings`, CalendarCheck], ["Notifications", `${B}/notifications`, Bell], ["Overdue Items", `${B}/task-board`, AlertCircle]].map(([label, to, Icon]) => (
              <Button key={label} variant="outline" className="justify-start" onClick={() => navigate(to)}><Icon className="h-4 w-4 mr-2" />{label}</Button>
            ))}
          </div>
        </Section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6" data-testid="task-analytics">
        <ChartCard title="Tasks by Status" subtitle="Live Task Board counts">
          <BarChartView data={effectiveTaskAnalytics?.by_status || []} xKey="name" keys={[{ key: "value", name: "Tasks" }]} />
        </ChartCard>
        <ChartCard title="Tasks by Service" subtitle="Tasks linked to each service">
          <BarChartView data={effectiveTaskAnalytics?.by_service || []} xKey="name" keys={[{ key: "value", name: "Tasks" }]} />
        </ChartCard>
        <ChartCard title="Employee / Consultant Workload" subtitle="Open tasks per assignee">
          <BarChartView data={effectiveTaskAnalytics?.workload || []} xKey="name" keys={[{ key: "value", name: "Active Tasks" }]} />
        </ChartCard>
        <ChartCard title="Task Completion Trend" subtitle="Existing task analytics">
          <LineChartView data={effectiveTaskAnalytics?.trend || taskAnalytics?.trend || []} xKey="m" keys={[{ key: "created", name: "Created" }, { key: "completed", name: "Completed" }]} />
        </ChartCard>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
        <ChartCard title="Monthly Revenue" testId="chart-revenue"><AreaChartView data={charts.revenue_monthly || []} xKey="m" keys={[{ key: "revenue", name: "Revenue" }]} /></ChartCard>
        <ChartCard title="Service-wise Invoice Counts"><DonutChartView data={serviceData} /></ChartCard>
        <ChartCard title="Payment Status"><DonutChartView data={paymentData} /></ChartCard>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <ChartCard title="Booking Status"><DonutChartView data={statusData} /></ChartCard>
        <ChartCard title="Customer Growth"><BarChartView data={charts.customer_growth || []} xKey="m" keys={[{ key: "customers", name: "New Customers" }]} /></ChartCard>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <ChartCard title="GST vs Income Tax Filing Trend"><LineChartView data={charts.filing_trend || []} xKey="m" keys={[{ key: "gst", name: "GST" }, { key: "itr", name: "ITR" }]} /></ChartCard>
        <ChartCard title="Employee Performance"><BarChartView data={charts.performance || []} xKey="name" keys={[{ key: "score", name: "Score" }]} /></ChartCard>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <ActivityFeed items={(summary?.projects || []).slice().sort((a, b) => String(b.updated_at || b.start_date || "").localeCompare(String(a.updated_at || a.start_date || ""))).slice(0, 5).map(p => ({ title: `${p.name} — ${p.status}`, time: p.due_date ? `Due ${p.due_date}` : "Active", tag: p.service_type || "Project" }))} />
        <DueDatesWidget items={(summary?.projects || []).filter(p => p.due_date).slice(0, 6).map(p => ({ title: p.name, date: p.due_date, type: p.service_type || "Project" }))} />
      </div>
      <RemindersWidget />
    </div>
  );
}
