import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen, ArrowLeft, ShieldCheck, RefreshCw, CircleAlert, ReceiptText, Users, FileChartColumn } from "lucide-react";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import "./ledger.css";

export default function LedgerDashboard() {
  const { user } = useAuth();
  const [status, setStatus] = useState("checking");
  const [message, setMessage] = useState("");

  const checkAccess = async () => {
    setStatus("checking"); setMessage("");
    try {
      await api.get("/customer/ledger/access");
      setStatus("ready");
    } catch (error) {
      setStatus(error.response?.status === 403 ? "denied" : error.response?.status === 401 ? "session" : "unavailable");
      setMessage(error.response?.data?.detail || "We could not verify Ledger access. Please retry.");
    }
  };
  useEffect(() => { checkAccess(); }, []);

  if (status !== "ready") return <main className="ledger-shell"><section className="ledger-message">
    {status === "checking" ? <RefreshCw className="ledger-message-icon spin" /> : <CircleAlert className="ledger-message-icon" />}
    <h1>{status === "checking" ? "Verifying Ledger access" : status === "denied" ? "My Ledger access is not enabled" : status === "session" ? "Your session has expired" : "Ledger is temporarily unavailable"}</h1>
    <p>{status === "checking" ? "Checking your NTAXCO customer account securely…" : message}</p>
    <div className="ledger-actions"><Button asChild><Link to="/customer/dashboard"><ArrowLeft size={16}/> Back to My Service</Link></Button>{status !== "denied" && status !== "session" && <Button variant="outline" onClick={checkAccess}>Try again</Button>}{status === "session" && <Button asChild><Link to="/login?portal=ledger">Sign in</Link></Button>}</div>
  </section></main>;

  return <main className="ledger-shell">
    <header className="ledger-top"><div className="ledger-brand"><span className="ledger-mark"><BookOpen size={22}/></span><div><strong>NTAXCO <span>LEDGER</span></strong><small>Financial workspace</small></div></div><nav><Link to="/customer/dashboard">My Service</Link><span className="ledger-active">My Ledger</span></nav></header>
    <section className="ledger-welcome"><div><p className="ledger-eyebrow"><ShieldCheck size={14}/> SECURE CUSTOMER WORKSPACE</p><h1>Welcome back{user?.name ? `, ${user.name}` : ""}</h1><p>Your Ledger workspace is connected to your NTAXCO account.</p></div><span className="ledger-secure"><ShieldCheck size={16}/> NTAXCO authenticated</span></section>
    <section className="ledger-notice"><CircleAlert size={19}/><div><strong>Ledger data connection required</strong><p>Your account is authorized, but this project does not yet include a verified customer-scoped Ledger data API. Financial balances and transactions are intentionally not fabricated or loaded from the standalone demo.</p></div></section>
    <section className="ledger-grid"><article><span><ReceiptText/></span><h2>Transactions & invoices</h2><p>View recorded financial activity once the secure Ledger API is configured.</p></article><article><span><Users/></span><h2>Business records</h2><p>Customer-specific records will be loaded only through the authenticated backend.</p></article><article><span><FileChartColumn/></span><h2>Reports</h2><p>Reports and exports become available when backed by authorized Ledger data.</p></article></section>
    <footer className="ledger-footer"><span>Ledger access uses your existing NTAXCO login.</span><Link to="/customer/dashboard"><ArrowLeft size={15}/> Switch to My Service</Link></footer>
  </main>;
}
