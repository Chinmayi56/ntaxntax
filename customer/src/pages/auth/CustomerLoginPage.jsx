import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Building2,
  CheckCircle2,
  Eye,
  EyeOff,
  FileCheck2,
  FileText,
  IndianRupee,
  Loader2,
  Mail,
  MessageSquare,
  BookOpen,
  ReceiptText,
  ShieldCheck,
  Smartphone,
  Sparkles,
  UserPlus,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { useAuth, isValidEmail, isValidIndianMobile } from "@/context/AuthContext";
import Logo from "@/components/shared/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { normalizePortal, postLoginPath, postSignupPath } from "@/lib/workspace";
import { safeNextPath } from "@/lib/billbookValidation";
import "./CustomerLoginPage.css";

const GSTIN_REGEX = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

const servicePills = [
  { label: "GST", icon: FileCheck2 },
  { label: "Income Tax", icon: IndianRupee },
  { label: "TDS", icon: ReceiptText },
  { label: "Documents", icon: FileText },
  { label: "Returns", icon: BarChart3 },
  { label: "Support", icon: MessageSquare },
];

const portalCopy = {
  service: {
    eyebrow: "NTAXCO CUSTOMER PORTAL",
    title: "Your tax & compliance workspace.",
    description: "Manage services, documents, returns, payments and communication with Nizam's Tax Consultancy — securely from one workspace.",
  },
  billbook: {
    eyebrow: "NTAXCO CUSTOMER WORKSPACE",
    title: "Your business billing workspace.",
    description: "Manage your business customers, invoices, inventory, payments and reports in My BillBook using your existing NTAXCO account.",
  },
};

const workspaces = [
  { id: "service", label: "My Service", caption: "Tax & compliance", icon: FileCheck2 },
  { id: "billbook", label: "My BillBook", caption: "Billing & business records", icon: BookOpen },
];

function PasswordField({ label, value, onChange, show, onToggle, placeholder, autoComplete }) {
  return (
    <div className="ntaxco-field">
      <Label>{label}</Label>
      <div className="relative mt-2">
        <Input
          className="ntaxco-input pr-12"
          type={show ? "text" : "password"}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          autoComplete={autoComplete}
          required
        />
        <motion.button
          whileTap={{ scale: 0.88 }}
          type="button"
          aria-label={show ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
          onClick={onToggle}
          className="absolute right-0 top-0 h-full px-3.5 text-slate-400 transition-colors hover:text-[#0B5ED7]"
        >
          <AnimatePresence mode="wait" initial={false}>
            <motion.span key={show ? "hide" : "show"} initial={{ opacity: 0, rotate: -20 }} animate={{ opacity: 1, rotate: 0 }} exit={{ opacity: 0, rotate: 20 }}>
              {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </motion.span>
          </AnimatePresence>
        </motion.button>
      </div>
    </div>
  );
}

export default function CustomerLoginPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const reduceMotion = useReducedMotion();
  const { user, login, register, sendOtp, verifyOtp, updateCustomerProfile } = useAuth();

  // The selected workspace lives in the URL (?portal=service|billbook) so it survives
  // refresh and can be linked to. Unknown / missing values fall back to My Service.
  const portal = normalizePortal(searchParams.get("portal"));
  // Optional return path (only /billbook/... subscription pages are accepted).
  const nextPath = safeNextPath(searchParams.get("next"));
  const [mode, setMode] = useState(searchParams.get("mode") === "signup" ? "signup" : "signin");
  const [method, setMethod] = useState("email");
  const [signupStep, setSignupStep] = useState(1);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [mobile, setMobile] = useState("");
  const [otp, setOtp] = useState("");
  const [otpSent, setOtpSent] = useState(false);
  const [name, setName] = useState("");
  const [gstin, setGstin] = useState("");
  const [loading, setLoading] = useState(false);
  const sendingOtpRef = useRef(false);
  const submittingRef = useRef(false);

  useEffect(() => {
    // An already-authenticated customer opening the login page goes straight to the
    // workspace they selected. During signup, the registration handler decides.
    if (user?.role === "customer" && mode === "signin") {
      navigate(nextPath || postLoginPath(portal), { replace: true });
    }
  }, [user, navigate, mode, portal, nextPath]);

  // Switching workspace only rewrites ?portal=. It does not touch auth state or tokens,
  // and the form fields below keep what the customer already typed.
  const selectPortal = (next) => {
    const target = normalizePortal(next);
    if (target === portal) return;
    // My BillBook starts with subscription plan selection; My Service is unchanged.
    if (target === "billbook") { navigate("/billbook/plans"); return; }
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.set("portal", target);
      return params;
    }, { replace: true });
  };

  const setAuthMode = (next) => {
    setMode(next);
    setSignupStep(1);
    setOtpSent(false);
    setOtp("");
  };

  const success = (u, message = "Welcome to NTAXCO!") => {
    toast.success(`${message} ${u?.name || ""}`.trim());
    // Same destination logic for password and OTP sign-in.
    navigate(nextPath || postLoginPath(portal), { replace: true });
  };

  const emailLogin = async (e) => {
    e.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setLoading(true);
    try {
      success(await login(email, password), "Welcome back");
    } catch (err) {
      toast.error(err.message);
    } finally {
      submittingRef.current = false;
      setLoading(false);
    }
  };

  const requestOtp = async () => {
    if (sendingOtpRef.current) return;
    sendingOtpRef.current = true;
    setLoading(true);
    try {
      await sendOtp(mobile);
      setOtpSent(true);
      setOtp("");
      toast.success("OTP sent to your mobile number.");
    } catch (err) {
      toast.error(err.message);
    } finally {
      sendingOtpRef.current = false;
      setLoading(false);
    }
  };

  const otpLogin = async (e) => {
    e.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setLoading(true);
    try {
      success(await verifyOtp(mobile, otp), "Welcome");
    } catch (err) {
      toast.error(err.message);
    } finally {
      submittingRef.current = false;
      setLoading(false);
    }
  };

  const submitSignup = async (e) => {
    e.preventDefault();
    if (submittingRef.current) return;
    if (!name.trim()) return toast.error("Please enter your full name.");
    if (!isValidIndianMobile(mobile)) return toast.error("Please enter a valid 10-digit Indian mobile number.");
    if (!isValidEmail(email)) return toast.error("Please enter a valid email address.");
    if (password.length < 8) return toast.error("Password must be at least 8 characters long.");
    if (password !== confirmPassword) return toast.error("Passwords do not match.");

    submittingRef.current = true;
    setLoading(true);
    try {
      const registeredUser = await register(name, email, mobile, password);
      toast.success(`Account created successfully. Welcome ${registeredUser?.name || name}!`);
      // Registration establishes the authenticated session; restore the selected workspace.
      navigate(nextPath || postSignupPath(portal), { replace: true });
    } catch (err) {
      toast.error(err.message);
    } finally {
      submittingRef.current = false;
      setLoading(false);
    }
  };

  const saveCustomerDetails = async (e) => {
    e.preventDefault();
    if (submittingRef.current) return;
    const cleanGstin = gstin.trim().toUpperCase();
    if (cleanGstin && !GSTIN_REGEX.test(cleanGstin)) {
      return toast.error("Please enter a valid 15-character GSTIN.");
    }
    submittingRef.current = true;
    setLoading(true);
    try {
      await updateCustomerProfile({ full_name: name, mobile, gstin: cleanGstin });
      toast.success("Customer details saved securely.");
      navigate(postSignupPath(portal), { replace: true });
    } catch (err) {
      toast.error(err.message);
    } finally {
      submittingRef.current = false;
      setLoading(false);
    }
  };

  const motionTransition = reduceMotion ? { duration: 0 } : { duration: 0.52, ease: [0.22, 1, 0.36, 1] };
  const copy = portalCopy[portal];

  return (
    <main className="ntaxco-login-shell">
      <div className="ntaxco-login-orb ntaxco-login-orb-one" />
      <div className="ntaxco-login-orb ntaxco-login-orb-two" />

      <section className="ntaxco-login-frame">
        <aside className="ntaxco-login-sidebar">
          <div>
            <div className="ntaxco-brand-lockup">
              <div className="ntaxco-logo-plate"><Logo className="h-14 sm:h-16" /></div>
              <div className="ntaxco-brand-caption">SECURE CUSTOMER ACCESS</div>
            </div>

            <div className="ntaxco-portal-label" id="ntaxco-workspace-label">Choose your workspace</div>
            <div className="ntaxco-portal-switcher" role="group" aria-labelledby="ntaxco-workspace-label" data-testid="workspace-selector">
              {workspaces.map(({ id, label, caption, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => selectPortal(id)}
                  aria-pressed={portal === id}
                  data-testid={`workspace-${id}`}
                  className={`ntaxco-portal-option ${portal === id ? "is-active" : ""}`}
                >
                  <span className="ntaxco-portal-icon"><Icon className="h-4 w-4" /></span>
                  <span><strong>{label}</strong><small>{caption}</small></span>
                  {portal === id && <span className="ntaxco-portal-indicator"><CheckCircle2 className="h-3.5 w-3.5" /></span>}
                </button>
              ))}
            </div>
          </div>

          <div className="ntaxco-security-note">
            <div className="ntaxco-security-icon"><ShieldCheck className="h-5 w-5" /></div>
            <div><strong>Protected by NTAXCO</strong><span>Your existing account and backend authentication are used.</span></div>
          </div>
        </aside>

        <section className="ntaxco-login-hero" aria-live="polite">
          <AnimatePresence mode="wait">
            <motion.div
              key={portal}
              initial={reduceMotion ? { opacity: 1 } : { opacity: 0, x: portal === "service" ? 18 : -18 }}
              animate={{ opacity: 1, x: 0 }}
              exit={reduceMotion ? { opacity: 1 } : { opacity: 0, x: portal === "service" ? -18 : 18 }}
              transition={motionTransition}
              className="ntaxco-hero-content"
            >
              <div className="ntaxco-hero-topline"><Sparkles className="h-4 w-4" /> {copy.eyebrow}</div>
              <h1>{copy.title}</h1>
              <p>{copy.description}</p>

              <div className="ntaxco-hero-art" aria-hidden="true">
                <div className="ntaxco-art-glow" />
                <motion.div className="ntaxco-art-card ntaxco-art-card-main" animate={reduceMotion ? {} : { y: [0, -7, 0], rotate: [0, 0.5, 0] }} transition={{ duration: 5.8, repeat: Infinity, ease: "easeInOut" }}>
                  <div className="ntaxco-art-card-header"><span>NTAXCO</span><span className="ntaxco-art-status">SECURE</span></div>
                  <div className="ntaxco-art-chart"><i /><i /><i /><i /><i /></div>
                  <div className="ntaxco-art-lines"><span /><span /><span /></div>
                </motion.div>
                <motion.div className="ntaxco-art-card ntaxco-art-card-float" animate={reduceMotion ? {} : { y: [0, 9, 0], rotate: [0, -2, 0] }} transition={{ duration: 4.6, repeat: Infinity, ease: "easeInOut", delay: 0.4 }}>
                  <FileCheck2 className="h-6 w-6 text-[#F5B800]" />
                  <div><strong>GST Ready</strong><span>Compliance tracked</span></div>
                </motion.div>
                <motion.div className="ntaxco-art-card ntaxco-art-card-float-two" animate={reduceMotion ? {} : { y: [0, -5, 0] }} transition={{ duration: 4.2, repeat: Infinity, ease: "easeInOut", delay: 0.8 }}>
                  <ShieldCheck className="h-5 w-5 text-[#0B5ED7]" /><span>Encrypted access</span>
                </motion.div>
              </div>

              <div className="ntaxco-service-pills">
                {servicePills.map(({ label, icon: Icon }, index) => (
                  <motion.span key={label} initial={reduceMotion ? {} : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...motionTransition, delay: index * 0.035 }}><Icon className="h-3.5 w-3.5" />{label}</motion.span>
                ))}
              </div>
            </motion.div>
          </AnimatePresence>
        </section>

        <section className="ntaxco-auth-panel">
          <div className="ntaxco-mobile-brand"><div className="ntaxco-logo-plate"><Logo className="h-12" /></div></div>
          <button type="button" className="ntaxco-back-link" onClick={() => navigate("/customer")}><ArrowLeft className="h-4 w-4" /> Back to website</button>

          <div className="ntaxco-auth-heading">
            <span data-testid="auth-workspace-label">{portal === "service" ? "My Service" : "My BillBook"}</span>
            <h2>{mode === "signup" ? (signupStep === 2 ? "Complete your profile" : "Create your account") : "Welcome to NTAXCO"}</h2>
            <p>{mode === "signup" ? (signupStep === 2 ? "Add your GSTIN and customer details to finish setup." : "One secure account for your NTAXCO customer workspace.") : "Sign in securely with your existing NTAXCO customer account."}</p>
          </div>


          <div className="ntaxco-auth-tabs" role="tablist" aria-label="Authentication mode">
            <button type="button" onClick={() => setAuthMode("signin")} className={mode === "signin" ? "is-active" : ""}><ShieldCheck className="h-4 w-4" />Sign In</button>
            <button type="button" onClick={() => setAuthMode("signup")} className={mode === "signup" ? "is-active" : ""}><UserPlus className="h-4 w-4" />Sign Up</button>
          </div>

          <AnimatePresence mode="wait">
            {mode === "signin" ? (
              <motion.div key={`signin-${method}`} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={motionTransition}>
                <div className="ntaxco-method-tabs">
                  <button type="button" onClick={() => { setMethod("email"); setOtpSent(false); }} className={method === "email" ? "is-active" : ""}><Mail className="h-4 w-4" /> Email</button>
                  <button type="button" onClick={() => setMethod("mobile")} className={method === "mobile" ? "is-active" : ""}><Smartphone className="h-4 w-4" /> Mobile OTP</button>
                </div>

                {method === "email" ? (
                  <form onSubmit={emailLogin} className="ntaxco-form">
                    <div className="ntaxco-field"><Label>Email address</Label><Input className="ntaxco-input mt-2" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" required /></div>
                    <PasswordField label="Password" value={password} onChange={(e) => setPassword(e.target.value)} show={showPassword} onToggle={() => setShowPassword((v) => !v)} placeholder="Enter your password" autoComplete="current-password" />
                    <Button className="ntaxco-primary-button" size="lg" disabled={loading || !isValidEmail(email)}>{loading ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Signing In...</> : <>Sign In <ArrowRight className="ml-2 h-4 w-4" /></>}</Button>
                  </form>
                ) : (
                  <div className="ntaxco-form">
                    <div className="ntaxco-field"><Label>Mobile number</Label><div className="relative mt-2"><span className="ntaxco-prefix">+91</span><Input className="ntaxco-input ntaxco-mobile-input" inputMode="numeric" maxLength={10} value={mobile} onChange={(e) => setMobile(e.target.value.replace(/\D/g, "").slice(0, 10))} placeholder="10-digit mobile number" /></div></div>
                    {!otpSent ? (
                      <Button type="button" className="ntaxco-primary-button" size="lg" onClick={requestOtp} disabled={loading || !isValidIndianMobile(mobile)}>{loading ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Sending OTP...</> : <>Send OTP <ArrowRight className="ml-2 h-4 w-4" /></>}</Button>
                    ) : (
                      <form onSubmit={otpLogin} className="space-y-4">
                        <div className="ntaxco-otp-box"><div className="flex items-center justify-between"><Label>Enter 6-digit OTP</Label><span className="text-xs text-slate-400">Sent via secure verification</span></div><div className="mt-3 flex justify-center"><InputOTP maxLength={6} value={otp} onChange={setOtp} autoFocus><InputOTPGroup>{[0, 1, 2, 3, 4, 5].map((i) => <InputOTPSlot key={i} index={i} />)}</InputOTPGroup></InputOTP></div></div>
                        <Button className="ntaxco-primary-button" size="lg" disabled={loading || otp.length !== 6}>{loading ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Verifying OTP...</> : <>Verify & Sign In <ArrowRight className="ml-2 h-4 w-4" /></>}</Button>
                        <button type="button" className="ntaxco-secondary-link" onClick={() => { setOtpSent(false); setOtp(""); }}>Use another number</button>
                      </form>
                    )}
                  </div>
                )}
              </motion.div>
            ) : (
              <motion.div key={`signup-${signupStep}`} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} transition={motionTransition}>
                {signupStep === 1 ? (
                  <form onSubmit={submitSignup} className="ntaxco-form">
                    <div className="ntaxco-field"><Label>Customer name</Label><Input className="ntaxco-input mt-2" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your full name" autoComplete="name" required /></div>
                    <div className="ntaxco-field"><Label>Mobile number</Label><div className="relative mt-2"><span className="ntaxco-prefix">+91</span><Input className="ntaxco-input ntaxco-mobile-input" inputMode="numeric" maxLength={10} value={mobile} onChange={(e) => setMobile(e.target.value.replace(/\D/g, "").slice(0, 10))} placeholder="10-digit mobile number" required /></div></div>
                    <div className="ntaxco-field"><Label>Email address</Label><Input className="ntaxco-input mt-2" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" required /></div>
                    <div className="grid sm:grid-cols-2 gap-3">
                      <PasswordField label="Password" value={password} onChange={(e) => setPassword(e.target.value)} show={showPassword} onToggle={() => setShowPassword((v) => !v)} placeholder="At least 8 characters" autoComplete="new-password" />
                      <PasswordField label="Confirm password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} show={showConfirmPassword} onToggle={() => setShowConfirmPassword((v) => !v)} placeholder="Re-enter password" autoComplete="new-password" />
                    </div>
                    {password && <div className="ntaxco-password-hint"><span className={password.length >= 8 ? "ok" : "bad"}>{password.length >= 8 ? <CheckCircle2 /> : <XCircle />} 8+ characters</span><span className={password === confirmPassword && confirmPassword ? "ok" : "bad"}>{password === confirmPassword && confirmPassword ? <CheckCircle2 /> : <XCircle />} Passwords match</span></div>}
                    <Button className="ntaxco-primary-button" size="lg" disabled={loading || !isValidEmail(email) || !isValidIndianMobile(mobile) || password.length < 8 || password !== confirmPassword}>{loading ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Creating Account...</> : <>Continue <ArrowRight className="ml-2 h-4 w-4" /></>}</Button>
                    <p className="ntaxco-form-footnote"><ShieldCheck className="h-3.5 w-3.5" /> Your account uses the existing NTAXCO authentication backend.</p>
                  </form>
                ) : (
                  <form onSubmit={saveCustomerDetails} className="ntaxco-form">
                    <div className="ntaxco-stepper"><span className="done">1</span><i /><span className="active">2</span><strong>Customer details</strong></div>
                    <div className="ntaxco-details-card"><div className="ntaxco-details-icon"><Building2 className="h-5 w-5" /></div><div><strong>Welcome, {name.split(" ")[0] || "Customer"}</strong><span>Your account is ready. Add the business GSTIN now so NTAXCO can keep your records connected.</span></div></div>
                    <div className="ntaxco-field"><Label>Customer name</Label><Input className="ntaxco-input mt-2" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your full name" required /></div>
                    <div className="ntaxco-field"><Label>Mobile number</Label><div className="relative mt-2"><span className="ntaxco-prefix">+91</span><Input className="ntaxco-input ntaxco-mobile-input" inputMode="numeric" maxLength={10} value={mobile} onChange={(e) => setMobile(e.target.value.replace(/\D/g, "").slice(0, 10))} placeholder="10-digit mobile number" required /></div></div>
                    <div className="ntaxco-field"><Label>GST Number (GSTIN)</Label><Input className="ntaxco-input mt-2 uppercase" value={gstin} onChange={(e) => setGstin(e.target.value.toUpperCase().replace(/[^0-9A-Z]/g, "").slice(0, 15))} placeholder="22AAAAA0000A1Z5" maxLength={15} autoComplete="off" /><span className="ntaxco-field-help">15 characters. Example format: 22AAAAA0000A1Z5</span></div>
                    <Button className="ntaxco-primary-button" size="lg" disabled={loading || !name.trim() || !isValidIndianMobile(mobile)}>{loading ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Saving Customer Details...</> : <>Save Customer Details <ArrowRight className="ml-2 h-4 w-4" /></>}</Button>
                  </form>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          <div className="ntaxco-auth-footer"><ShieldCheck className="h-4 w-4" /><span>Secure authentication • Existing NTAXCO APIs • Customer data stays account-scoped</span></div>
        </section>
      </section>
    </main>
  );
}
