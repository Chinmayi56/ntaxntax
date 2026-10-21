import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider } from "@/context/AuthContext";
import { ProtectedRoute } from "@/routes/guards";
import CustomerLayout from "@/components/layout/CustomerLayout";
import NotFound from "@/pages/NotFound";
import CustomerLoginPage from "@/pages/auth/CustomerLoginPage";
import CustomerLanding from "@/pages/customer/CustomerLanding";
import CustomerDashboard from "@/pages/customer/CustomerDashboard";
import CustomerServices from "@/pages/customer/CustomerServices";
import ServiceDetails from "@/pages/customer/ServiceDetails";
import CustomerReturns from "@/pages/customer/CustomerReturns";
import CustomerInvoices from "@/pages/customer/CustomerInvoices";
import CustomerDocuments from "@/pages/customer/CustomerDocuments";
import CustomerSupport from "@/pages/customer/CustomerSupport";
import CustomerProfile from "@/pages/customer/CustomerProfile";
import CustomerBookings from "@/pages/customer/CustomerBookings";
import CustomerTds from "@/pages/customer/CustomerTds";
import CustomerMessages from "@/pages/customer/CustomerMessages";
import CustomerReports from "@/pages/customer/CustomerReports";
import CustomerCalculator from "@/pages/customer/CustomerCalculator";
import NotificationsPage from "@/components/shared/NotificationsPage";
import BillBookApp from "@/billbook/BillBookApp";
import { BillBookPlansPage, BillBookDetailsPage, BillBookPaymentStatusPage } from "@/pages/billbook/BillBookSubscription";

const Protected = ({ children }) => <ProtectedRoute>{children}</ProtectedRoute>;
const BillBookProtected = ({ children }) => <ProtectedRoute loginPath="/login?portal=billbook">{children}</ProtectedRoute>;

export default function App() {
  return (
    <AuthProvider>
      <Toaster position="bottom-right" richColors closeButton />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate to="/customer" replace />} />
          <Route path="/login" element={<CustomerLoginPage />} />
          {/* Legacy Ledger URLs now lead to My BillBook; NTAXCO remains the sole login. */}
          <Route path="/ledger/login" element={<Navigate to="/login?portal=billbook" replace />} />
          <Route path="/ledger/signup" element={<Navigate to="/login?portal=billbook&mode=signup" replace />} />
          <Route path="/ledger/*" element={<Navigate to="/my-billbook" replace />} />
          {/* My BillBook subscription journey: plans -> details -> Razorpay -> verified status -> /login?portal=billbook */}
          <Route path="/billbook" element={<Navigate to="/billbook/plans" replace />} />
          <Route path="/billbook/plans" element={<BillBookPlansPage />} />
          <Route path="/billbook/details" element={<BillBookDetailsPage />} />
          <Route path="/billbook/payment-status" element={<BillBookProtected><BillBookPaymentStatusPage /></BillBookProtected>} />
          <Route path="/my-billbook/*" element={<BillBookProtected><BillBookApp /></BillBookProtected>} />

          {/* The company website is public. Customer-only pages are protected individually. */}
          <Route path="/customer" element={<CustomerLayout />}>
            <Route index element={<CustomerLanding />} />
            <Route path="services" element={<CustomerServices />} />
            <Route path="services/:id" element={<ServiceDetails />} />

            <Route path="dashboard" element={<CustomerDashboard />} />
            <Route path="bookings" element={<Protected><CustomerBookings /></Protected>} />
            <Route path="gst" element={<Protected><CustomerReturns type="gst" /></Protected>} />
            <Route path="income-tax" element={<CustomerReturns type="itr" />} />
            <Route path="tds" element={<Protected><CustomerTds /></Protected>} />
            <Route path="projects" element={<Navigate to="/customer/profile" replace />} />
            <Route path="invoices" element={<Protected><CustomerInvoices /></Protected>} />
            <Route path="documents" element={<Protected><CustomerDocuments /></Protected>} />
            <Route path="support" element={<Protected><CustomerSupport /></Protected>} />
            <Route path="messages" element={<Protected><CustomerMessages /></Protected>} />
            <Route path="reports" element={<Protected><CustomerReports /></Protected>} />
            <Route path="calculator" element={<Protected><CustomerCalculator /></Protected>} />
            <Route path="notifications" element={<Protected><NotificationsPage base="/customer" portal="Customer" padded /></Protected>} />
            <Route path="profile" element={<Protected><CustomerProfile /></Protected>} />

            <Route path="*" element={<NotFound />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
