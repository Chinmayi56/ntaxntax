import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import LoadingScreen from "@/components/shared/LoadingScreen";

export function ProtectedRoute({ children, loginPath = "/login" }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <LoadingScreen label="Checking your session..." />;
  if (!user) return <Navigate to={loginPath} replace state={{ from: location }} />;
  if (user.role !== "customer") return <Navigate to={loginPath} replace />;
  return children;
}

export function PublicRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <LoadingScreen />;
  if (user?.role === "customer") return <Navigate to="/customer" replace />;
  return children;
}
