import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider } from "@/context/AuthContext";
import { ProtectedRoute, PublicRoute } from "@/routes/guards";
import PortalLayout from "@/components/layout/PortalLayout";
import NotFound from "@/pages/NotFound";
import LoginPage from "@/pages/auth/LoginPage";
import WebLedgerPage from "@/pages/ledger/WebLedgerPage";
import ModulePlaceholder from "@/pages/ModulePlaceholder";
import NotificationsPage from "@/components/shared/NotificationsPage";
import SettingsPage from "@/pages/admin/SettingsPage";
import AdminProfile from "@/pages/admin/AdminProfile";
import AgentBookings from "@/pages/agent/AgentBookings";
import AgentTasks from "@/pages/agent/Tasks";
import AdminDashboard from "@/pages/admin/AdminDashboard";
import EmployeeDashboard from "@/pages/employee/EmployeeDashboard";
import AgentDashboard from "@/pages/agent/AgentDashboard";
import EmployeeManagement from "@/pages/admin/EmployeeManagement";
import AdminLeaveRequests from "@/pages/admin/AdminLeaveRequests";
import CustomerManagement from "@/pages/admin/CustomerManagement";
import AgentManagement from "@/pages/admin/AgentManagement";
import PaymentDashboard from "@/pages/admin/PaymentDashboard";
import Bookings from "@/pages/admin/Bookings";
import Projects from "@/pages/admin/Projects";
import Services from "@/pages/admin/Services";
import PrimeServicesManagement from "@/pages/admin/PrimeServicesManagement";
import PrimeCustomersManagement from "@/pages/admin/PrimeCustomersManagement";
import Invoices from "@/pages/admin/Invoices";
import GstModule from "@/pages/admin/GstModule";
import IncomeTaxModule from "@/pages/admin/IncomeTaxModule";
import TdsModule from "@/pages/admin/TdsModule";
import RocModule from "@/pages/admin/RocModule";
import AccountingDashboard from "@/pages/admin/AccountingDashboard";
import Reports from "@/pages/admin/Reports";
import TaskBoard from "@/pages/admin/TaskBoard";
import SecurityOverview from "@/pages/admin/security/SecurityOverview";
import LoginActivity from "@/pages/admin/security/LoginActivity";
import ActiveSessions from "@/pages/admin/security/ActiveSessions";
import Attendance from "@/pages/employee/Attendance";
import Leave from "@/pages/employee/Leave";
import Tasks from "@/pages/employee/Tasks";
import Payslips from "@/pages/employee/Payslips";
import EmployeeCustomers from "@/pages/employee/EmployeeCustomers";
import EmployeeBookings from "@/pages/employee/EmployeeBookings";
import EmployeeCompliance from "@/pages/employee/EmployeeCompliance";
import Leads from "@/pages/agent/Leads";
import Onboarding from "@/pages/agent/Onboarding";
import Appointments from "@/pages/agent/Appointments";
import Commission from "@/pages/agent/Commission";
import CollectionTable from "@/components/shared/CollectionTable";
import CalendarView from "@/components/shared/CalendarView";
import DynamicModulePage from "@/pages/admin/DynamicModulePage";
import PerformancePage from "@/components/shared/PerformancePage";
import ProfilePage from "@/components/shared/ProfilePage";
import RemindersWidget from "@/components/shared/RemindersWidget";
import Revenue from "@/pages/admin/Revenue";
import DashboardRevenueDetails from "@/pages/admin/DashboardRevenueDetails";
import BillBookAdmin from "@/pages/admin/BillBookAdmin";
import BillBookOperations from "@/pages/admin/BillBookOperations";
// Customer portal
import { NAV } from "@/lib/constants";

const DASHBOARDS = { admin: <AdminDashboard />, employee: <EmployeeDashboard />, agent: <AgentDashboard /> };

const ADMIN_MODULES = {
  billbook: <BillBookAdmin />,
  "billbook/plans": <BillBookAdmin />,
  "billbook/subscriptions": <BillBookAdmin />,
  "billbook/subscribers": <BillBookAdmin />,
  "billbook/payments": <BillBookAdmin />,
  "billbook/settings": <BillBookAdmin />,
  "billbook/history": <BillBookOperations />,
  "billbook/renewals": <BillBookOperations />,
  "billbook/trials": <BillBookOperations />,
  "billbook/events": <BillBookOperations />,
  "billbook/reports": <BillBookOperations />,
  employees: <EmployeeManagement />,
  "leave-requests": <AdminLeaveRequests />,
  customers: <CustomerManagement />,
  bookings: <Bookings />,
  projects: <Projects />,
  services: <Services />,
  "prime-services": <PrimeServicesManagement />,
  "prime-customers": <PrimeCustomersManagement />,
  invoices: <Invoices />,
  gst: <GstModule />,
  "income-tax": <IncomeTaxModule />,
  tds: <TdsModule />,
  roc: <RocModule />,
  accounting: <AccountingDashboard />,
  reports: <Reports />,
  "task-board": <TaskBoard />,
  agents: <AgentManagement />,
  payments: <PaymentDashboard />,
  revenue: <Revenue view="summary" />,
  "revenue/employees": <Revenue view="employees" />,
  "revenue/agents": <Revenue view="agents" />,
  "revenue/customers": <Revenue view="customers" />,
  "employee-revenue": <DashboardRevenueDetails kind="employee" />,
  "customer-revenue": <DashboardRevenueDetails kind="customer" />,
  "agent-revenue": <DashboardRevenueDetails kind="agent" />,
  "revenue/today": <Revenue view="today" />,
  "revenue/pending": <Revenue view="pending" />,
  "revenue/month": <Revenue view="month" />,
  "revenue/year": <Revenue view="year" />,
  documents: <CollectionTable preset="admin-documents" />,
  notifications: <NotificationsPage base="/admin" portal="Super Admin" />,
  "security/overview": <SecurityOverview />,
  "security/login-activity": <LoginActivity />,
  "security/sessions": <ActiveSessions />,
  "admin-profile": <AdminProfile />,
  settings: <SettingsPage />,
};

const EMPLOYEE_MODULES = {
  attendance: <Attendance />,
  leave: <Leave />,
  tasks: <Tasks />,
  payslips: <Payslips />,
  projects: <CollectionTable preset="emp-projects" />,
  customers: <EmployeeCustomers />,
  bookings: <EmployeeBookings />,
  calendar: <CalendarView role="employee" />,
  meetings: <CollectionTable preset="emp-meetings" />,
  gst: <EmployeeCompliance kind="gst" />,
  "income-tax": <EmployeeCompliance kind="income-tax" />,
  tds: <EmployeeCompliance kind="tds" />,
  roc: <EmployeeCompliance kind="roc" />,
  documents: <CollectionTable preset="emp-documents" />,
  notifications: <NotificationsPage base="/employee" portal="Employee" />,
  performance: <PerformancePage role="employee" />,
  profile: <ProfilePage role="employee" />,
};

const AGENT_MODULES = {
  leads: <Leads />,
  onboarding: <Onboarding />,
  appointments: <Appointments />,
  commission: <Commission />,
  customers: <CollectionTable preset="agent-customers" />,
  bookings: <AgentBookings />,
  tasks: <AgentTasks />,
  calendar: <CalendarView role="agent" />,
  meetings: <CollectionTable preset="agent-meetings" />,
  projects: <CollectionTable preset="agent-projects" />,
  documents: <CollectionTable preset="agent-documents" />,
  notifications: <NotificationsPage base="/agent" portal="Tax Consultant" />,
  performance: <PerformancePage role="agent" />,
  reports: <Reports />,
  "task-board": <TaskBoard />,
  profile: <ProfilePage role="agent" />,
};

function PortalRoutes({ role, overrides = {}, dynamicElement = null }) {
  const items = NAV[role] || [];
  return (
    <Route path={`/${role}`} element={<ProtectedRoute role={role}><PortalLayout role={role} /></ProtectedRoute>}>
      <Route index element={<Navigate to={`/${role}/dashboard`} replace />} />
      <Route path="dashboard" element={DASHBOARDS[role]} />
      {items.filter((it) => !it.path.endsWith("/dashboard")).map((it) => {
        // Use the path relative to the portal base (not just the last
        // segment) so multi-segment routes like /admin/security/overview
        // nest correctly instead of colliding at /admin/overview.
        const seg = it.path.replace(`/${role}/`, "");
        return <Route key={it.path} path={seg} element={overrides[seg] || <ModulePlaceholder role={role} title={it.label} />} />;
      })}
      {/* Sub-pages that are not sidebar items (e.g. Revenue card destinations such as
          revenue/employees, revenue/today). They exist in the overrides map but were never
          registered, so React Router fell through to the "*" NotFound route. Register every
          override key that the sidebar did not already cover. */}
      {Object.keys(overrides)
        .filter((seg) => !items.some((it) => it.path.replace(`/${role}/`, "") === seg) && seg !== "dashboard")
        .map((seg) => <Route key={`extra-${seg}`} path={seg} element={overrides[seg]} />)}
      {/* Admin-created dynamic modules (Settings → Modules / Dynamic
          Configuration) live at a single-segment slug, e.g. /admin/client-feedback.
          React Router ranks static segments (the routes above) higher than this
          ":moduleSlug" param, so it only ever catches slugs that aren't already
          a real route — and it resolves the module by slug on every load, so
          direct navigation/refresh always works without depending on the
          sidebar having loaded first. */}
      {dynamicElement && <Route path=":moduleSlug" element={dynamicElement} />}
      <Route path="*" element={<NotFound />} />
    </Route>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Toaster position="bottom-right" richColors closeButton />

      <BrowserRouter>
        <Routes>

          {/* Login / Portal selection */}
          <Route path="/" element={<PublicRoute><LoginPage /></PublicRoute>} />
          <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />
          <Route path="/login/:role" element={<PublicRoute><LoginPage /></PublicRoute>} />

          {/* Web Ledger is separately protected by a backend entitlement check. */}
          <Route path="/admin/web-ledger" element={<ProtectedRoute role="admin"><WebLedgerPage /></ProtectedRoute>} />

          {/* Admin Portal */}
          {PortalRoutes({
            role: "admin",
            overrides: ADMIN_MODULES,
            dynamicElement: <DynamicModulePage />,
          })}

          {/* Employee Portal */}
          {PortalRoutes({
            role: "employee",
            overrides: EMPLOYEE_MODULES,
          })}

          {/* Agent Portal */}
          {PortalRoutes({
            role: "agent",
            overrides: AGENT_MODULES,
          })}

          {/* 404 */}
          <Route path="*" element={<NotFound />} />

        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}