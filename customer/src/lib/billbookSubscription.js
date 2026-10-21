import api, { formatApiError } from "@/lib/api";
import { parsePlansResponse } from "@/lib/billbookPlans";

// All prices, plan validity, ownership and payment status come from the backend. The browser only
// sends the selected plan id and the customer's details; it never sends a price or a status.
const DRAFT_KEY = "billbook_subscription_draft"; // form values only - never payment/subscription state

export async function fetchPlans() {
  const { data } = await api.get("/billbook/plans");
  return parsePlansResponse(data); // throws on an unexpected body so the page shows an error, not "No plans"
}
export async function fetchSubscriptionStatus() {
  const { data } = await api.get("/billbook/subscription/status");
  return data.data;
}
export async function createGuestCheckout(payload) {
  const { data } = await api.post("/billbook/guest-checkout", payload);
  return data.data;
}
export async function verifyGuestCheckout(payload) {
  const { data } = await api.post("/billbook/guest-checkout/verify", payload);
  return data.data;
}
export async function claimGuestCheckout(payload) {
  const { data } = await api.post("/billbook/guest-checkout/claim", payload);
  return data.data;
}
export async function createCheckout(payload) {
  const { data } = await api.post("/billbook/subscription/checkout", payload);
  return data.data;
}
export async function verifySubscriptionPayment(response) {
  const { data } = await api.post("/billbook/subscription/verify", {
    razorpay_order_id: response.razorpay_order_id,
    razorpay_payment_id: response.razorpay_payment_id,
    razorpay_signature: response.razorpay_signature,
  });
  return data.data;
}
export async function fetchOrderStatus(orderId) {
  const { data } = await api.get(`/billbook/subscription/order/${encodeURIComponent(orderId)}`);
  return data.data;
}
export async function cancelCheckoutAttempt(orderId) {
  const { data } = await api.post("/billbook/subscription/cancel-attempt", { order_id: orderId });
  return data.data;
}

export function apiMessage(error, fallback) {
  return formatApiError(error?.response?.data?.detail) || error?.message || fallback;
}
export function apiFieldErrors(error) {
  const detail = error?.response?.data?.detail;
  return detail && typeof detail === "object" && detail.fields ? detail.fields : null;
}
export function apiErrorMessage(error, fallback) {
  const detail = error?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.message) return detail.message;
  return apiMessage(error, fallback);
}

export function loadDraft() {
  try { return JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "null") || {}; } catch { return {}; }
}
export function saveDraft(values) {
  try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(values)); } catch { /* storage unavailable */ }
}
export function clearDraft() {
  try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
}

// Opens the existing Razorpay Checkout (checkout.js is already loaded by public/index.html).
// A popup callback is NOT proof of payment: the caller must send the response to the backend verify API.
export function openRazorpayCheckout(order, { onSuccess, onDismiss, onFailure }) {
  if (typeof window === "undefined" || !window.Razorpay) {
    throw new Error("Razorpay Checkout could not be loaded. Check your connection and try again.");
  }
  const rzp = new window.Razorpay({
    key: order.key_id,
    order_id: order.order_id,
    amount: order.amount,
    currency: order.currency,
    name: "NTAXCO - My BillBook",
    description: `My BillBook subscription - ${order.plan?.name || ""}`,
    prefill: order.prefill,
    theme: { color: "#0B5ED7" },
    handler: onSuccess,
    modal: { ondismiss: onDismiss },
  });
  rzp.on("payment.failed", (res) => onFailure?.(res?.error?.description || "Payment failed."));
  rzp.open();
}
