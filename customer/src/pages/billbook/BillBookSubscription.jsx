import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, ArrowRight, BookOpen, Check, CheckCircle2, Clock, Loader2, RefreshCw, ShieldCheck, XCircle,
} from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import Logo from "@/components/shared/Logo";
import { inr } from "@/lib/utils";
import { validateDetails, normalizeMobile, formatDate } from "@/lib/billbookValidation";
import {
  apiErrorMessage, apiFieldErrors, cancelCheckoutAttempt, clearDraft, createCheckout, createGuestCheckout, verifyGuestCheckout, claimGuestCheckout, fetchOrderStatus, fetchPlans,
  fetchSubscriptionStatus, loadDraft, openRazorpayCheckout, saveDraft, verifySubscriptionPayment,
} from "@/lib/billbookSubscription";
import { PlansResponseError, adminActionDiagnostics } from "@/lib/billbookPlans";
import "./BillBookSubscription.css";

const LOGIN_PATH = "/login?portal=billbook";

function Shell({ children, step }) {
  const steps = ["Plans", "Details", "Payment", "Login"];
  return (
    <main className="bbs-shell">
      <header className="bbs-header">
        <Link to="/customer" className="bbs-logo" aria-label="NTAXCO home"><Logo className="h-10" /></Link>
        <div className="bbs-brand"><BookOpen className="h-4 w-4" aria-hidden="true" /> My BillBook</div>
        <ol className="bbs-steps" aria-label="Subscription progress">
          {steps.map((label, i) => (
            <li key={label} className={i + 1 === step ? "is-current" : i + 1 < step ? "is-done" : ""} aria-current={i + 1 === step ? "step" : undefined}>
              <span>{i + 1 < step ? <Check className="h-3 w-3" aria-hidden="true" /> : i + 1}</span>{label}
            </li>
          ))}
        </ol>
      </header>
      <div className="bbs-body">{children}</div>
    </main>
  );
}

function Notice({ tone = "info", icon: Icon = AlertTriangle, title, children, action }) {
  return (
    <div className={`bbs-notice bbs-notice-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
      <div><strong>{title}</strong>{children && <p>{children}</p>}</div>
      {action}
    </div>
  );
}

function usePlans() {
  const [state, setState] = useState({ loading: true, error: "", plans: [], missing: [], actions: [], types: [], hidden: false });
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: "" }));
    try {
      const d = await fetchPlans();
      setState({ loading: false, error: "", plans: d.plans, missing: d.missing, actions: adminActionDiagnostics(d), types: d.types, hidden: d.hidden });
    } catch (e) {
      // A failed/unparseable request is an ERROR state, never an empty plan list.
      setState((s) => ({ ...s, loading: false, plans: [], error: e instanceof PlansResponseError ? e.message : apiErrorMessage(e, "Unable to load subscription plans.") }));
    }
  }, []);
  useEffect(() => {
    load();
    const onFocus = () => load();
    const onPageShow = () => load();
    const onVisibility = () => { if (document.visibilityState === "visible") load(); };
    window.addEventListener("focus", onFocus);
    window.addEventListener("pageshow", onPageShow);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("pageshow", onPageShow);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);
  return { ...state, reload: load };
}

function useSubscriptionStatus(enabled) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(Boolean(enabled));
  useEffect(() => {
    let alive = true;
    if (!enabled) { setStatus(null); setLoading(false); return undefined; }
    setLoading(true);
    fetchSubscriptionStatus().then((d) => alive && setStatus(d)).catch(() => alive && setStatus(null)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [enabled]);
  return { status, loading };
}

/* ------------------------------------------------------------------ Step 2: plans */
export function BillBookPlansPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { user } = useAuth();
  const isCustomer = user?.role === "customer";
  const { loading, error, plans, actions, hidden, reload } = usePlans();
  const { status } = useSubscriptionStatus(isCustomer);
  useEffect(() => {
    if (!isCustomer) return;
    const draft = loadDraft();
    if (!draft.guest_checkout_id || !draft.guest_claim_token || draft.guest_claim_attempted) return;
    saveDraft({ ...draft, guest_claim_attempted: true });
    claimGuestCheckout({ checkout_id: draft.guest_checkout_id, claim_token: draft.guest_claim_token })
      .then(() => { clearDraft(); toast.success("Your verified My BillBook purchase is linked to this account."); })
      .catch((e) => { saveDraft({ ...loadDraft(), guest_claim_attempted: false }); toast.error(apiErrorMessage(e, "Could not link purchase. Sign in with the email or mobile used at checkout.")); });
  }, [isCustomer]);
  const [trialPlans, setTrialPlans] = useState([]);
  const [trialBusy, setTrialBusy] = useState("");
  const trialAutoStarted = useRef("");
  useEffect(() => { let live = true; api.get("/billbook/trial-plans").then((r) => { if (live) setTrialPlans(r?.data?.data?.plans || []); }).catch(() => { if (live) setTrialPlans([]); }); return () => { live = false; }; }, []);

  // If a visitor selected a trial, NTAXCO login returns here with activate_trial=<plan id>.
  // Activation happens only after the customer is authenticated by the existing login flow.
  const pendingTrialId = params.get("activate_trial") || "";
  useEffect(() => {
    if (!isCustomer || !pendingTrialId || !trialPlans.some((p) => p.id === pendingTrialId)) return undefined;
    if (trialAutoStarted.current === pendingTrialId) return undefined;
    trialAutoStarted.current = pendingTrialId;
    let live = true;
    setTrialBusy(pendingTrialId);
    api.post("/billbook/trials/activate", { plan_id: pendingTrialId })
      .then(() => {
        if (!live) return;
        toast.success("Free trial activated. No payment was taken.");
        navigate("/my-billbook", { replace: true });
      })
      .catch((e) => {
        if (!live) return;
        toast.error(apiErrorMessage(e, "Unable to activate this free trial."));
        navigate("/billbook/plans", { replace: true });
      })
      .finally(() => { if (live) setTrialBusy(""); });
    return () => { live = false; };
  }, [isCustomer, pendingTrialId, trialPlans, navigate]);

  const startTrial = async (trial) => {
    if (!isCustomer) {
      const returnPath = `/billbook/plans?activate_trial=${encodeURIComponent(trial.id)}`;
      navigate(`/login?portal=billbook&next=${encodeURIComponent(returnPath)}`);
      return;
    }
    setTrialBusy(trial.id);
    try {
      await api.post("/billbook/trials/activate", { plan_id: trial.id });
      toast.success("Free trial activated. No payment was taken.");
      navigate("/my-billbook", { replace: true });
    } catch (e) {
      toast.error(apiErrorMessage(e, "Unable to activate this free trial."));
    } finally {
      setTrialBusy("");
    }
  };
  const [selected, setSelected] = useState(loadDraft().plan_id || "");
  const state = status?.state;
  const sub = status?.subscription;

  const purchase = (plan) => {
    setSelected(plan.id);
    saveDraft({ ...loadDraft(), plan_id: plan.id });
    navigate(`/billbook/details?plan=${encodeURIComponent(plan.id)}${state === "active" ? "&renew=true" : ""}`);
  };

  return (
    <Shell step={1}>
      <section className="bbs-intro">
        <h1>Choose your My BillBook plan</h1>
        <p>Billing, customers, inventory, payments and reports for your business, secured with your NTAXCO account.</p>
      </section>

      {params.get("reason") === "subscription" && (
        <Notice tone="warn" title="An active subscription is required">Choose a plan to open My BillBook.</Notice>
      )}
      {state === "active" && (
        <Notice tone="success" icon={CheckCircle2} title={`Active plan: ${sub?.plan_name}`}
          action={<Link className="bbs-btn bbs-btn-primary" to={LOGIN_PATH}>Continue to My BillBook <ArrowRight className="h-4 w-4" /></Link>}>
          Valid until {formatDate(sub?.expires_at)}. You can renew below; the new term starts when this one ends.
        </Notice>
      )}
      {state === "expired" && <Notice tone="warn" title={`Your ${sub?.plan_name} plan expired on ${formatDate(sub?.expires_at)}`}>Select a plan to renew.</Notice>}
      {state === "pending" && sub?.order_id && (
        <Notice tone="info" icon={Clock} title="A payment is awaiting confirmation"
          action={<Link className="bbs-btn" to={`/billbook/payment-status?order=${encodeURIComponent(sub.order_id)}`}>Check payment status</Link>}>
          Access is granted only after your payment is confirmed. You can safely start a new attempt if this one was not completed.
        </Notice>
      )}
      {(state === "failed" || state === "cancelled") && <Notice tone="warn" icon={XCircle} title="Your last payment attempt was not completed">No subscription was activated and you were not charged for an activation. Choose a plan to try again.</Notice>}
      {!loading && !error && actions.length > 0 && (
        <Notice tone="warn" title="Some plans are not configured yet">{actions.map((a) => a.message).join(" ")} An administrator must complete their configuration (price and duration) before they can be purchased.</Notice>
      )}

      {loading && <div className="bbs-grid" aria-busy="true">{[0, 1, 2, 3].map((i) => <div key={i} className="bbs-card bbs-skeleton" />)}</div>}
      {error && (
        <Notice tone="error" icon={XCircle} title="Could not load plans" action={<button className="bbs-btn" onClick={reload}><RefreshCw className="h-4 w-4" /> Retry</button>}>{error}</Notice>
      )}
      {!loading && !error && plans.length === 0 && hidden && <Notice title="Plans are temporarily unavailable">New subscriptions are paused right now. Please check back soon.</Notice>}
      {!loading && !error && plans.length === 0 && !hidden && actions.length > 0 && <Notice title="No plans can be purchased yet">Plans are being set up. Please check back soon.</Notice>}
      {!loading && !error && plans.length === 0 && !hidden && actions.length === 0 && <Notice title="No plans available">Subscription plans are not available right now.</Notice>}

      {!loading && !error && plans.filter((p) => !["trial", "free_trial"].includes(String(p.plan_type || "").toLowerCase())).length > 0 && (
        <div className="bbs-grid" role="list">
          {plans.filter((p) => !["trial", "free_trial"].includes(String(p.plan_type || "").toLowerCase())).map((plan) => {
            const isSelected = selected === plan.id;
            return (
              <article key={plan.id} role="listitem" className={`bbs-card ${isSelected ? "is-selected" : ""} ${plan.configured ? "" : "is-unavailable"}`} aria-label={`${plan.name} plan`}>
                {isSelected && <span className="bbs-badge">Selected</span>}
                <h2>{plan.name}</h2>
                <p className="bbs-period">Billed once for {plan.billing_period}</p>
                <div className="bbs-price">
                  {plan.configured ? <><strong>{inr(plan.price)}</strong><span>/ {plan.billing_period}</span></> : <strong className="bbs-price-missing">Price not configured</strong>}
                </div>
                <ul>{(plan.features || []).map((f) => <li key={f}><Check className="h-4 w-4" aria-hidden="true" />{f}</li>)}</ul>
                <button className="bbs-btn bbs-btn-primary" disabled={!plan.configured} onClick={() => purchase(plan)} aria-pressed={isSelected}>
                  {plan.configured ? "Purchase Plan" : "Unavailable"}
                </button>
              </article>
            );
          })}
        </div>
      )}
      {trialPlans.length > 0 && <section className="bbs-trials"><h2>Free trials</h2><p>Start an eligible trial without entering payment details. Eligibility is checked securely by NTAXCO.</p><div className="bbs-grid">{trialPlans.map((trial) => <article key={trial.id} className="bbs-card"><h2>{trial.name}</h2><p>{trial.description}</p><div className="bbs-price"><strong>Free</strong><span>/ {trial.billing_period || (trial.duration_days ? `${trial.duration_days} days` : `${trial.duration_months} months`)}</span></div><ul>{(trial.features || []).map((f) => <li key={f}><Check className="h-4 w-4" aria-hidden="true" />{f}</li>)}</ul><button className="bbs-btn bbs-btn-primary" disabled={!!trialBusy} onClick={() => startTrial(trial)}>{trialBusy === trial.id ? "Starting trial…" : "Start Free Trial"}</button></article>)}</div></section>}
      <p className="bbs-foot"><ShieldCheck className="h-4 w-4" aria-hidden="true" /> Payments are processed by Razorpay. Access is activated only after NTAXCO verifies your payment.
        <Link to="/login">Back to workspace selection</Link></p>
    </Shell>
  );
}

/* ------------------------------------------------------------------ Step 3 + 4: details and payment */
const FIELDS = [
  { name: "full_name", label: "Full name", autoComplete: "name" },
  { name: "mobile", label: "Mobile number", autoComplete: "tel-national", inputMode: "tel", hint: "10-digit Indian mobile number" },
  { name: "email", label: "Email address", autoComplete: "email", type: "email" },
  { name: "business_name", label: "Business name", autoComplete: "organization" },
];

export function BillBookDetailsPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { user } = useAuth();
  const isCustomer = user?.role === "customer";
  const planId = params.get("plan") || loadDraft().plan_id || "";
  const renew = params.get("renew") === "true";
  const { loading, error, plans, types, reload } = usePlans();
  const plan = useMemo(() => plans.find((p) => p.id === planId), [plans, planId]);

  const [values, setValues] = useState(() => ({ full_name: "", mobile: "", email: "", business_name: "", business_type: "", gstin: "", ...loadDraft() }));
  const [errors, setErrors] = useState({});
  const [touched, setTouched] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const submittingRef = useRef(false);

  // Pre-fill from the signed-in NTAXCO account without overwriting what the customer already typed.
  useEffect(() => {
    if (!isCustomer) return;
    setValues((v) => ({
      ...v,
      full_name: v.full_name || user.name || user.full_name || "",
      email: v.email || user.email || "",
      mobile: v.mobile || normalizeMobile(user.mobile || ""),
    }));
  }, [isCustomer, user]);
  useEffect(() => { saveDraft({ ...values, plan_id: planId }); }, [values, planId]);

  const setField = (name, value) => {
    setValues((v) => ({ ...v, [name]: name === "gstin" ? value.toUpperCase() : value }));
    if (touched[name] || errors[name]) setErrors((e) => ({ ...e, [name]: validateDetails({ ...values, [name]: value }, types)[name] }));
  };
  const blur = (name) => { setTouched((t) => ({ ...t, [name]: true })); setErrors((e) => ({ ...e, [name]: validateDetails(values, types)[name] })); };

  const submit = async (e) => {
    e.preventDefault();
    if (submittingRef.current) return;
    setMessage("");
    const found = validateDetails(values, types);
    setErrors(found);
    setTouched({ full_name: true, mobile: true, email: true, business_name: true, business_type: true, gstin: true });
    if (Object.keys(found).length) { setMessage("Please correct the highlighted fields."); return; }
    submittingRef.current = true;
    setSubmitting(true);
    try {
      const details = { plan_id: plan.id, renew: renew ? "true" : "false", ...values, mobile: normalizeMobile(values.mobile) };
      const order = isCustomer ? await createCheckout(details) : await createGuestCheckout(details);
      if (!isCustomer) saveDraft({ ...loadDraft(), ...values, plan_id: plan.id, guest_checkout_id: order.checkout_id, guest_claim_token: order.claim_token });
      openRazorpayCheckout(order, {
        onSuccess: (response) => {
          if (isCustomer) clearDraft();
          navigate(`/billbook/payment-status?order=${encodeURIComponent(order.order_id)}${!isCustomer ? `&guest=${encodeURIComponent(order.checkout_id)}` : ""}`, { state: { response, guest: !isCustomer, checkoutId: order.checkout_id } });
        },
        onDismiss: async () => {
          if (isCustomer) { try { await cancelCheckoutAttempt(order.order_id); } catch { /* the order simply stays pending */ } }
          setMessage("Payment was cancelled. Nothing was activated and you can try again.");
          submittingRef.current = false; setSubmitting(false);
        },
        onFailure: (text) => { setMessage(`${text} You can retry in the payment window.`); },
      });
    } catch (err) {
      const fields = apiFieldErrors(err);
      if (fields) setErrors(fields);
      setMessage(apiErrorMessage(err, "Unable to start the payment."));
      submittingRef.current = false; setSubmitting(false);
    }
  };

  let body;
  if (loading) body = <div className="bbs-card bbs-skeleton bbs-form-skeleton" aria-busy="true" />;
  else if (error) body = <Notice tone="error" icon={XCircle} title="Could not load plan" action={<button className="bbs-btn" onClick={reload}>Retry</button>}>{error}</Notice>;
  else if (!plan) body = <Notice tone="warn" title="Plan not found">The selected plan does not exist. <Link to="/billbook/plans">Choose a plan</Link></Notice>;
  else if (!plan.configured) body = <Notice tone="warn" title="This plan is not available">{plan.configuration_issue} <Link to="/billbook/plans">Choose another plan</Link></Notice>;
  else body = (
    <div className="bbs-split">
      <aside className="bbs-summary" aria-label="Selected plan">
        <span className="bbs-badge">Selected plan</span>
        <h2>{plan.name}</h2>
        <div className="bbs-price"><strong>{inr(plan.price)}</strong><span>/ {plan.billing_period}</span></div>
        <ul>{(plan.features || []).map((f) => <li key={f}><Check className="h-4 w-4" aria-hidden="true" />{f}</li>)}</ul>
        <p className="bbs-why">We need these details to register your My BillBook subscription and issue your payment receipt. Your NTAXCO account is reused - no second account is created.</p>
      </aside>
      <form className="bbs-card bbs-form" onSubmit={submit} noValidate aria-label="Customer details">
        <h2>Customer details</h2>
        {FIELDS.map(({ name, label, hint, ...rest }) => (
          <div className="bbs-field" key={name}>
            <label htmlFor={`bbs-${name}`}>{label} <span aria-hidden="true">*</span></label>
            <input id={`bbs-${name}`} value={values[name]} onChange={(e) => setField(name, e.target.value)} onBlur={() => blur(name)}
              aria-invalid={Boolean(errors[name])} aria-describedby={errors[name] ? `bbs-${name}-err` : hint ? `bbs-${name}-hint` : undefined} required {...rest} />
            {hint && !errors[name] && <small id={`bbs-${name}-hint`}>{hint}</small>}
            {errors[name] && <small className="bbs-err" id={`bbs-${name}-err`}>{errors[name]}</small>}
          </div>
        ))}
        <div className="bbs-field">
          <label htmlFor="bbs-business_type">Business type <span aria-hidden="true">*</span></label>
          <select id="bbs-business_type" value={values.business_type} onChange={(e) => setField("business_type", e.target.value)} onBlur={() => blur("business_type")}
            aria-invalid={Boolean(errors.business_type)} aria-describedby={errors.business_type ? "bbs-business_type-err" : undefined} required>
            <option value="">Select business type</option>
            {types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          {errors.business_type && <small className="bbs-err" id="bbs-business_type-err">{errors.business_type}</small>}
        </div>
        <div className="bbs-field">
          <label htmlFor="bbs-gstin">GSTIN <em>(optional)</em></label>
          <input id="bbs-gstin" value={values.gstin} maxLength={15} onChange={(e) => setField("gstin", e.target.value)} onBlur={() => blur("gstin")}
            aria-invalid={Boolean(errors.gstin)} aria-describedby={errors.gstin ? "bbs-gstin-err" : undefined} autoComplete="off" />
          {errors.gstin && <small className="bbs-err" id="bbs-gstin-err">{errors.gstin}</small>}
        </div>

        {message && <Notice tone="error" icon={AlertTriangle} title={message} />}

        <div className="bbs-actions bbs-actions-end">
          <button type="button" className="bbs-btn" onClick={() => navigate("/billbook/plans")} disabled={submitting}><ArrowLeft className="h-4 w-4" /> Back</button>
          <button type="submit" className="bbs-btn bbs-btn-primary" disabled={submitting}>
            {submitting ? <><Loader2 className="h-4 w-4 bbs-spin" /> Starting payment...</> : <>Continue to Payment <ArrowRight className="h-4 w-4" /></>}
          </button>
        </div>
      </form>
    </div>
  );

  return <Shell step={isCustomer ? 3 : 2}><section className="bbs-intro"><h1>Your details</h1><p>Review your plan and tell us about your business.</p></section>{body}</Shell>;
}

/* ------------------------------------------------------------------ Step 4/5: status + activation */
export function BillBookPaymentStatusPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const orderId = params.get("order") || "";
  const response = location.state?.response;
  const [result, setResult] = useState({ state: "checking", subscription: null, error: "" });
  const [tries, setTries] = useState(0);
  const verifiedRef = useRef(false);

  const check = useCallback(async () => {
    setResult((r) => ({ ...r, state: r.state === "pending" ? "pending" : "checking", error: "" }));
    try {
      let data;
      // The popup response is only forwarded to the backend, which verifies the signature and
      // confirms the payment with Razorpay. Nothing is activated on the strength of this callback.
      if (response && !verifiedRef.current) {
        verifiedRef.current = true;
        if (location.state?.guest) {
          const draft = loadDraft();
          data = await verifyGuestCheckout({ checkout_id: location.state.checkoutId || params.get("guest"), claim_token: draft.guest_claim_token, ...response });
          if (data.state === "active") { setResult({ state: "active", subscription: null, guest: true, error: "" }); return; }
        } else data = await verifySubscriptionPayment(response);
      } else data = await fetchOrderStatus(orderId);
      setResult({ state: data.state, subscription: data.subscription, error: "" });
    } catch (e) {
      verifiedRef.current = false;
      setResult((r) => ({ ...r, state: "error", error: apiErrorMessage(e, "We could not confirm your payment status.") }));
    }
  }, [orderId, response, location.state, params]);

  useEffect(() => { if (orderId) check(); }, [orderId, check]);
  useEffect(() => {
    if (result.state !== "pending" || tries >= 10) return undefined;
    const t = setTimeout(async () => {
      setTries((n) => n + 1);
      try { const d = await fetchOrderStatus(orderId); setResult({ state: d.state, subscription: d.subscription, error: "" }); } catch { /* keep polling */ }
    }, 4000);
    return () => clearTimeout(t);
  }, [result.state, tries, orderId]);
  useEffect(() => {
    if (result.state !== "active") return undefined;
    toast.success("My BillBook subscription activated.");
    const t = setTimeout(() => navigate(result.guest ? `/login?portal=billbook&next=${encodeURIComponent("/billbook/plans")}` : LOGIN_PATH, { replace: true }), 3500);
    return () => clearTimeout(t);
  }, [result.state, result.guest, navigate]);

  const sub = result.subscription;
  let panel;
  if (!orderId) panel = <Notice tone="warn" title="No payment to show">Start from the plan selection page. <Link to="/billbook/plans">Choose a plan</Link></Notice>;
  else if (result.state === "checking") panel = <div className="bbs-status"><Loader2 className="h-10 w-10 bbs-spin" aria-hidden="true" /><h2>Confirming your payment...</h2><p>Please do not close this page.</p></div>;
  else if (result.state === "active") panel = (
    <div className="bbs-status bbs-status-ok" role="status">
      <CheckCircle2 className="h-12 w-12" aria-hidden="true" />
      <h2>Subscription activated</h2>
      <p>{result.guest ? "Your payment is verified. Sign in or create an NTAXCO customer account using the same email or mobile number used at checkout. Your purchase can then be linked securely." : <>{sub?.plan_name} plan is active until <strong>{formatDate(sub?.expires_at)}</strong>. Opening My BillBook login...</>}</p>
      <Link className="bbs-btn bbs-btn-primary" to={LOGIN_PATH} replace>Continue to My BillBook login <ArrowRight className="h-4 w-4" /></Link>
    </div>
  );
  else if (result.state === "pending") panel = (
    <div className="bbs-status" role="status"><Clock className="h-12 w-12" aria-hidden="true" /><h2>Payment pending</h2>
      <p>Your bank or Razorpay has not confirmed this payment yet. Access is granted only after confirmation; this page checks automatically.</p>
      <button className="bbs-btn" onClick={async () => { setTries(0); try { const d = await fetchOrderStatus(orderId); setResult({ state: d.state, subscription: d.subscription, error: "" }); } catch (e) { setResult((r) => ({ ...r, error: apiErrorMessage(e, "Still unable to confirm.") })); } }}><RefreshCw className="h-4 w-4" /> Check again</button>
      {tries >= 10 && <p className="bbs-small">Still pending. If money was debited it will be confirmed or refunded automatically; you can also contact NTAXCO support with order {orderId}.</p>}
    </div>
  );
  else if (result.state === "failed" || result.state === "cancelled") panel = (
    <div className="bbs-status bbs-status-bad" role="alert"><XCircle className="h-12 w-12" aria-hidden="true" /><h2>{result.state === "failed" ? "Payment failed" : "Payment cancelled"}</h2>
      <p>No subscription was activated. {sub?.failure_reason || ""}</p>
      <Link className="bbs-btn bbs-btn-primary" to="/billbook/plans">Try again</Link></div>
  );
  else panel = (
    <div className="bbs-status bbs-status-bad" role="alert"><AlertTriangle className="h-12 w-12" aria-hidden="true" /><h2>We could not confirm your payment</h2>
      <p>{result.error || "Please try again."} Your subscription has not been activated.</p>
      <button className="bbs-btn bbs-btn-primary" onClick={check}><RefreshCw className="h-4 w-4" /> Check again</button></div>
  );

  return <Shell step={result.state === "active" ? 4 : 3}>{panel}</Shell>;
}
