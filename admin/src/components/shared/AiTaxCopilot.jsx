import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, X, Send, Sparkles, Loader2, ExternalLink, BarChart3, RefreshCw } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import api, { formatApiError } from "@/lib/api";
import { toast } from "sonner";

const ADMIN_QUICK = [
  "How many services do we have?",
  "Show all active employees.",
  "What is our revenue this month?",
  "Show pending bookings.",
  "Show pending invoices.",
  "Show customers with pending payments.",
];

const AGENT_QUICK = [
  "Show my leads",
  "Who should I follow up with today?",
  "What appointments do I have today?",
  "Show my pending tasks",
  "Show my customers",
  "How much commission did I earn this month?",
  "How am I performing this month?",
  "What should I work on today?",
];

const WELCOME = {
  role: "assistant",
  content: "Hi! I’m the NTAXCO AI Copilot.",
};

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

function ResultView({ result, onNavigate }) {
  if (!result || result.type === "empty") return null;

  if (result.type === "stat") {
    return (
      <div className="mt-2 rounded-xl border border-zinc-200 bg-zinc-50 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-400">{result.label}</p>
        <p className="mt-1 text-2xl font-bold text-zinc-900">
          {result.currency === "INR" ? INR.format(Number(result.value || 0)) : Number(result.value || 0).toLocaleString("en-IN")}
        </p>
      </div>
    );
  }

  if (result.type === "revenue") {
    return (
      <div className="mt-2 rounded-xl border border-zinc-200 bg-zinc-50 p-3">
        <div className="flex items-end justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-400">{result.label}</p>
            <p className="mt-1 text-2xl font-bold text-zinc-900">{INR.format(Number(result.value || 0))}</p>
          </div>
          <span className="text-xs text-zinc-500">{Number(result.transactions || 0).toLocaleString("en-IN")} transactions</span>
        </div>
        {Array.isArray(result.breakdown) && result.breakdown.length > 0 && (
          <div className="mt-3 space-y-2">
            {result.breakdown.map((item) => (
              <div key={item.name} className="flex items-center justify-between text-xs">
                <span className="truncate text-zinc-600">{item.name}</span>
                <span className="font-semibold text-zinc-800">{INR.format(Number(item.amount || 0))}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (result.type === "chart") {
    const rows = Array.isArray(result.rows) ? result.rows : [];
    const metric = rows.length && Object.keys(rows[0]).find((key) => key !== "name") ;
    const max = Math.max(...rows.map((row) => Number(row[metric] || 0)), 1);
    return (
      <div className="mt-2 rounded-xl border border-zinc-200 bg-zinc-50 p-3">
        <div className="flex items-center gap-2 mb-3">
          <BarChart3 className="h-4 w-4 text-zinc-500" />
          <p className="text-xs font-semibold text-zinc-700">{result.label}</p>
        </div>
        <div className="space-y-2.5">
          {rows.map((row) => (
            <div key={row.name}>
              <div className="mb-1 flex justify-between gap-2 text-[11px]">
                <span className="truncate text-zinc-600">{row.name}</span>
                <span className="font-semibold text-zinc-800">
                  {metric === "revenue" ? INR.format(Number(row[metric] || 0)) : Number(row[metric] || 0).toLocaleString("en-IN")}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-zinc-200">
                <div className="h-full rounded-full bg-[#FFB800]" style={{ width: `${Math.max(4, (Number(row[metric] || 0) / max) * 100)}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (result.type === "table") {
    const rows = Array.isArray(result.rows) ? result.rows : [];
    const columns = Array.isArray(result.columns) ? result.columns : [];
    const keys = columns.map((column) => ({
      label: column,
      key: column.toLowerCase().replace(/[^a-z0-9]+(.)/g, (_, c) => c.toUpperCase()),
    }));
    const keyMap = {
      invoice: ["invoice_no", "invoice"],
      customer: ["customer_name", "customer"],
      amount: ["amount", "billed"],
      pending: ["pending_amount", "pending"],
      paid: ["paid_amount", "paid"],
      booking: ["booking"],
      service: ["service"],
      status: ["status"],
      date: ["date"],
      name: ["name"],
      designation: ["designation"],
      department: ["department"],
      email: ["email"],
      priority: ["priority"],
      employee: ["employee"],
    };
    return (
      <div className="mt-2 overflow-hidden rounded-xl border border-zinc-200 bg-white">
        <div className="max-h-52 overflow-auto">
          <table className="w-full text-left text-[11px]">
            <thead className="sticky top-0 bg-zinc-50 text-zinc-500">
              <tr>{columns.map((column) => <th key={column} className="whitespace-nowrap px-2.5 py-2 font-semibold">{column}</th>)}</tr>
            </thead>
            <tbody>
              {rows.slice(0, 25).map((row, index) => (
                <tr key={row.id || index} className="border-t border-zinc-100">
                  {keys.map(({ label, key }) => {
                    const candidates = [key, ...(keyMap[label.toLowerCase()] || [])];
                    const value = candidates.map((candidate) => row[candidate]).find((candidate) => candidate !== undefined && candidate !== null);
                    const display = ["amount", "pending", "paid", "billed"].includes(label.toLowerCase())
                      ? INR.format(Number(value || 0))
                      : String(value ?? "—");
                    return <td key={label} className="max-w-[150px] truncate px-2.5 py-2 text-zinc-700">{display}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return null;
}

export default function AiTaxCopilot({ role = "admin" }) {
  const navigate = useNavigate();
  const isAgent = role === "agent";
  const quickActions = isAgent ? AGENT_QUICK : ADMIN_QUICK;
  const welcome = useMemo(() => ({ ...WELCOME, content: isAgent
    ? "Hello! I’m NTAXCO Agent AI. I can help with your leads, follow-ups, appointments, tasks, customers, bookings, commission and performance."
    : "Hi! I’m the NTAXCO AI Copilot. Ask me about live ERP data such as customers, employees, agents, services, bookings, tasks, invoices, payments or revenue."
  }), [isAgent]);
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState([welcome]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const sessionRef = useRef(
    (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : `sess-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
  const scrollRef = useRef(null);

  const resetConversation = () => {
    setMsgs([welcome]);
    setInput("");
    setLoading(false);
    sessionRef.current =
      (typeof crypto !== "undefined" && crypto.randomUUID)
        ? crypto.randomUUID()
        : `sess-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  };

  useEffect(() => {
    const handler = (e) => {
      setOpen(true);
      const p = e.detail?.prompt;
      if (p) setInput(p);
    };
    window.addEventListener("ntaxco:open-copilot", handler);
    return () => window.removeEventListener("ntaxco:open-copilot", handler);
  }, []);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, loading, open]);

  const conversationHistory = useMemo(
    () => msgs.slice(-10).map(({ role: messageRole, content }) => ({ role: messageRole, content })),
    [msgs]
  );

  const send = async (text) => {
    const q = (text ?? input).trim();
    if (!q || loading) return;
    const nextUserMessage = { role: "user", content: q };
    setMsgs((m) => [...m, nextUserMessage]);
    setInput("");
    setLoading(true);
    try {
      const { data } = await api.post("/copilot/chat", {
        message: q,
        session_id: sessionRef.current,
        history: conversationHistory,
      });
      const payload = data?.data || data;
      setMsgs((m) => [...m, {
        role: "assistant",
        content: payload?.reply || "I couldn't find a matching answer.",
        result: payload?.result,
        detailsRoute: payload?.details_route,
        disclaimer: payload?.disclaimer,
      }]);
    } catch (e) {
      const detail = e.response?.data?.detail;
      const err = formatApiError(detail) || "The NTAXCO AI Copilot is unavailable right now.";
      toast.error(err);
      setMsgs((m) => [...m, { role: "assistant", content: err }]);
    } finally {
      setLoading(false);
    }
  };

  if (role !== "admin" && role !== "agent") return null;

  return (
    <div className="fixed bottom-5 right-5 sm:bottom-6 sm:right-6 z-[70] flex flex-col items-end gap-3">
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 18, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.97 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="w-[calc(100vw-2rem)] sm:w-[430px] h-[min(650px,calc(100vh-7rem))] bg-white rounded-2xl shadow-2xl border border-zinc-200 flex flex-col overflow-hidden"
            data-testid="copilot-panel"
            role="dialog"
            aria-label="NTAXCO AI Copilot"
          >
            <div className="bg-[#0A2540] px-4 py-3.5 flex items-center justify-between">
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="relative h-9 w-9 shrink-0 rounded-xl bg-[#FFB800] flex items-center justify-center">
                  <Sparkles className="h-4 w-4 text-[#0A2540]" />
                  <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full bg-emerald-400 border-2 border-[#0A2540]" />
                </div>
                <div className="min-w-0">
                  <p className="font-heading font-bold text-white text-sm leading-tight">{isAgent ? "NTAXCO Agent AI" : "NTAXCO AI Copilot"}</p>
                  <p className="text-[11px] text-slate-300">{isAgent ? "Live ERP data · Agent-only access" : "Live ERP data · Admin access"}</p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={resetConversation}
                  data-testid="copilot-refresh"
                  className="h-8 w-8 rounded-lg text-slate-300 hover:bg-white/10 hover:text-white flex items-center justify-center transition-colors"
                  aria-label="Refresh Copilot conversation"
                  title="Refresh conversation"
                >
                  <RefreshCw className="h-4 w-4" />
                </button>
                <button
                  onClick={() => setOpen(false)}
                  data-testid="copilot-close"
                  className="h-8 w-8 rounded-lg text-slate-300 hover:bg-white/10 hover:text-white flex items-center justify-center transition-colors"
                  aria-label="Minimize Copilot"
                  title="Minimize Copilot"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div ref={scrollRef} className="flex-1 overflow-y-auto p-3.5 sm:p-4 space-y-3 bg-zinc-50">
              {msgs.map((m, i) => (
                <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[94%] min-w-0 ${m.role === "user" ? "" : "w-full"}`}>
                    <div className={`text-sm rounded-2xl px-3.5 py-2.5 whitespace-pre-wrap leading-relaxed shadow-sm ${
                      m.role === "user"
                        ? "bg-[#0A2540] text-white rounded-br-md ml-auto max-w-[90%]"
                        : "bg-white text-zinc-800 border border-zinc-200 rounded-bl-md"
                    }`}>
                      {m.content}
                    </div>
                    {m.role === "assistant" && (
                      <>
                        <ResultView result={m.result} onNavigate={navigate} />
                        {m.detailsRoute && (
                          <button
                            type="button"
                            onClick={() => navigate(m.detailsRoute)}
                            className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-zinc-700 hover:border-[#FFB800] hover:text-zinc-900 transition-colors"
                          >
                            View Details <ExternalLink className="h-3 w-3" />
                          </button>
                        )}
                        {m.disclaimer && <p className="mt-2 text-[10px] leading-4 text-zinc-400">{m.disclaimer}</p>}
                      </>
                    )}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex justify-start">
                  <div className="bg-white border border-zinc-200 rounded-2xl rounded-bl-md px-4 py-3 shadow-sm flex items-center gap-1.5">
                    <Loader2 className="h-4 w-4 animate-spin text-[#0A2540]" />
                    <span className="text-xs text-zinc-500">Checking live ERP data…</span>
                  </div>
                </div>
              )}
            </div>

            {msgs.length <= 1 && !loading && (
              <div className="px-3 pb-2.5 pt-1 flex flex-wrap gap-1.5 bg-zinc-50 border-t border-zinc-100">
                {quickActions.map((q) => (
                  <button key={q} onClick={() => send(q)} className="text-[11px] bg-white border border-zinc-200 rounded-full px-2.5 py-1.5 text-zinc-600 hover:border-[#FFB800] hover:text-[#0A2540] transition-colors" data-testid="copilot-quick">
                    {q}
                  </button>
                ))}
              </div>
            )}

            <div className="p-3 border-t border-zinc-200 bg-white">
              <div className="flex gap-2">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  placeholder={isAgent ? "Ask NTAXCO AI about your work…" : "Ask about your NTAXCO ERP…"}
                  disabled={loading}
                  rows={1}
                  className="min-w-0 flex-1 resize-none text-sm border border-zinc-300 rounded-xl px-3.5 py-2.5 focus:outline-none focus:ring-2 focus:ring-[#FFB800]/40 disabled:opacity-60"
                  data-testid="copilot-input"
                  aria-label="Ask NTAXCO AI Copilot"
                />
                <button onClick={() => send()} disabled={loading || !input.trim()} className="h-10 w-10 shrink-0 rounded-xl bg-[#FFB800] text-[#0A2540] flex items-center justify-center hover:bg-[#E5A600] transition-colors disabled:opacity-50" data-testid="copilot-send" aria-label="Send">
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <button
        onClick={() => setOpen((o) => !o)}
        className="h-14 w-14 sm:h-15 sm:w-15 rounded-full bg-[#0A2540] shadow-xl flex items-center justify-center text-[#FFB800] hover:scale-105 transition-transform ring-4 ring-[#0A2540]/10"
        title={isAgent ? "NTAXCO Agent AI" : "NTAXCO AI Copilot"}
        data-testid="copilot-button"
        aria-label={open ? "Minimize NTAXCO AI Copilot" : "Open NTAXCO AI Copilot"}
      >
        {open ? <X className="h-6 w-6" /> : <Bot className="h-7 w-7" />}
      </button>
    </div>
  );
}
