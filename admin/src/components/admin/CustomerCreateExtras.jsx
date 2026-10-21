import { useEffect, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { inr } from "@/lib/utils";
import { Upload, X, FileText, ImageIcon, Loader2, CheckCircle2, AlertCircle } from "lucide-react";

// ---------------------------------------------------------------------------------------------------------
// Shared constants / helpers used by the Add Customer dialog (CustomerManagement.jsx).
// ---------------------------------------------------------------------------------------------------------
export const TERM_KEYS = [
  { key: "term1", name: "Term 1", label: "Term 1 Payment" },
  { key: "term2", name: "Term 2", label: "Term 2 Payment" },
  { key: "term3", name: "Term 3", label: "Term 3 Payment" },
];
export const PAYMENT_METHODS = ["Cash", "UPI", "Bank Transfer", "Card", "Cheque", "Online Payment", "Other"];
export const emptyTerms = () => ({ term1: "", term2: "", term3: "" });

export const DOC_TYPES = [
  "PAN Card", "GST Certificate", "Aadhaar", "TAN Certificate", "CIN Certificate",
  "Company Registration Certificate", "Address Proof", "Other Document",
];
export const MAX_DOC_BYTES = 5 * 1024 * 1024;
export const MAX_DOC_FILES = 20;
const MIME_BY_EXT = {
  pdf: "application/pdf", jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp",
  doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};
const ALLOWED_MIMES = new Set(Object.values(MIME_BY_EXT));
export const DOC_ACCEPT = Object.keys(MIME_BY_EXT).map((e) => `.${e}`).join(",");

const extOf = (name) => (String(name).includes(".") ? String(name).split(".").pop().toLowerCase() : "");
export const isImageMime = (mime) => String(mime || "").startsWith("image/");

export function fileTypeLabel(file) {
  const ext = extOf(file.name);
  return ext ? ext.toUpperCase() : (file.type || "FILE");
}

export function formatBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(2)} MB`;
}

/** Best-guess document category from the file name (admin can change it). */
export function guessDocType(name) {
  const n = String(name || "").toLowerCase();
  if (/\bpan\b|pan[\s_-]?card/.test(n)) return "PAN Card";
  if (/gst/.test(n)) return "GST Certificate";
  if (/aadhaar|aadhar|adhar/.test(n)) return "Aadhaar";
  if (/\btan\b/.test(n)) return "TAN Certificate";
  if (/\bcin\b/.test(n)) return "CIN Certificate";
  if (/registration|incorporation|company/.test(n)) return "Company Registration Certificate";
  if (/address/.test(n)) return "Address Proof";
  return "Other Document";
}

/** Returns an error string for a file the backend would reject, or "" when it is acceptable. */
export function validateDocFile(file) {
  const ext = extOf(file.name);
  const mime = file.type || MIME_BY_EXT[ext] || "";
  if (!ALLOWED_MIMES.has(mime) || !MIME_BY_EXT[ext]) return "Unsupported file type. Allowed: PDF, JPG, JPEG, PNG, WebP, DOC, DOCX, XLSX.";
  if (file.size <= 0) return "The file is empty.";
  if (file.size > MAX_DOC_BYTES) return `File is too large (${formatBytes(file.size)}). Maximum size is 5 MB.`;
  return "";
}

export function resolveMime(file) {
  return file.type || MIME_BY_EXT[extOf(file.name)] || "application/octet-stream";
}

export function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.readAsDataURL(file);
  });
}

/**
 * Payment term parsing. Blank = skipped (no record). Returns { errors, entries, total }:
 *  - errors: { term1?: "Term 1 Payment cannot be negative." }
 *  - entries: [{ key, name, amount }] for every valid, non-blank term
 *  - total: sum of the valid entries
 */
export function parseTerms(terms, customTerms = []) {
  const errors = {};
  const entries = [];
  TERM_KEYS.forEach(({ key, name, label }) => {
    const raw = String(terms[key] ?? "").replace(/[₹,\s]/g, "");
    if (!raw) return;
    if (!/^-?\d+(\.\d+)?$/.test(raw)) { errors[key] = `${label} must be a valid number.`; return; }
    const value = Number(raw);
    if (value < 0) { errors[key] = `${label} cannot be negative.`; return; }
    if (value === 0) { errors[key] = `${label} must be greater than zero (leave it blank to skip this term).`; return; }
    if (!/^\d+(\.\d{1,2})?$/.test(raw)) { errors[key] = `${label} can have at most 2 decimal places.`; return; }
    if (value > 1e9) { errors[key] = `${label} is too large.`; return; }
    entries.push({ key, name, amount: Math.round(value * 100) / 100 });
  });
  // Custom terms (Advance, Milestone 1, Final Payment ...): a row left completely blank is skipped.
  const seenNames = new Set(TERM_KEYS.map((t) => t.name.toLowerCase()));
  customTerms.forEach((row) => {
    const key = `custom-${row.id}`;
    const name = String(row.name ?? "").trim();
    const raw = String(row.amount ?? "").replace(/[₹,\s]/g, "");
    if (!name && !raw) return;
    if (!name) { errors[key] = "Enter a name for this payment term."; return; }
    if (seenNames.has(name.toLowerCase())) { errors[key] = `A payment term named "${name}" already exists.`; return; }
    if (!raw) { errors[key] = `${name} needs an amount (or remove the row).`; return; }
    if (!/^\d+(\.\d{1,2})?$/.test(raw)) { errors[key] = `${name} must be a valid amount with at most 2 decimal places.`; return; }
    const value = Number(raw);
    if (value <= 0) { errors[key] = `${name} must be greater than zero.`; return; }
    if (value > 1e9) { errors[key] = `${name} is too large.`; return; }
    seenNames.add(name.toLowerCase());
    entries.push({ key, name, amount: Math.round(value * 100) / 100 });
  });
  const total = Math.round(entries.reduce((sum, e) => sum + e.amount, 0) * 100) / 100;
  return { errors, entries, total };
}

// ---------------------------------------------------------------------------------------------------------
// Payment Terms section
// ---------------------------------------------------------------------------------------------------------
export function PaymentTermsSection({ terms, onChange, meta, onMetaChange, errors, total, disabled, lockedKeys = [], customTerms = [], onCustomChange, onCustomAdd, onCustomRemove }) {
  return (
    <div className="sm:col-span-2" data-testid="payment-terms-section">
      <div className="-mx-1 pt-2 pb-1.5 text-xs font-bold uppercase tracking-wide text-royal border-b border-zinc-200 mb-3">Payment Terms</div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {TERM_KEYS.map(({ key, label }) => (
          <div key={key}>
            <Label className="text-sm font-medium text-zinc-700" htmlFor={`pt-${key}`}>{label}</Label>
            <div className="relative mt-1.5">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-zinc-500">₹</span>
              <Input
                id={`pt-${key}`} data-testid={`payment-${key}`} inputMode="decimal" autoComplete="off" placeholder="0.00"
                value={terms[key]} disabled={disabled || lockedKeys.includes(key)}
                onChange={(e) => onChange(key, e.target.value)}
                aria-invalid={!!errors[key]}
                className={`pl-7 ${errors[key] ? "border-red-400" : "border-zinc-300"}`}
              />
            </div>
            {errors[key] && <p className="mt-1 text-[11px] text-red-600" role="alert">{errors[key]}</p>}
            {lockedKeys.includes(key) && <p className="mt-1 text-[11px] text-emerald-700">Saved</p>}
          </div>
        ))}
        {customTerms.map((row) => {
          const key = `custom-${row.id}`;
          const locked = lockedKeys.includes(key);
          return (
            <div key={key} className="sm:col-span-3" data-testid={`payment-${key}`}>
              <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto] gap-3 items-end">
                <div>
                  <Label className="text-sm font-medium text-zinc-700">Custom Term Name</Label>
                  <Input className="mt-1.5 border-zinc-300" placeholder="e.g. Advance, Milestone 1, Final Payment" value={row.name} disabled={disabled || locked} onChange={(e) => onCustomChange(row.id, "name", e.target.value)} aria-invalid={!!errors[key]} />
                </div>
                <div>
                  <Label className="text-sm font-medium text-zinc-700">Amount</Label>
                  <div className="relative mt-1.5">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-zinc-500">₹</span>
                    <Input inputMode="decimal" autoComplete="off" placeholder="0.00" className="pl-7 border-zinc-300" value={row.amount} disabled={disabled || locked} onChange={(e) => onCustomChange(row.id, "amount", e.target.value)} />
                  </div>
                </div>
                {locked ? <span className="pb-2 text-[11px] text-emerald-700">Saved</span> : (
                  <Button type="button" variant="outline" className="border-zinc-300" disabled={disabled} onClick={() => onCustomRemove(row.id)}><X className="h-4 w-4" /></Button>
                )}
              </div>
              {errors[key] && <p className="mt-1 text-[11px] text-red-600" role="alert">{errors[key]}</p>}
            </div>
          );
        })}
        {onCustomAdd && (
          <div className="sm:col-span-3">
            <Button type="button" variant="outline" size="sm" className="border-zinc-300" disabled={disabled} onClick={onCustomAdd} data-testid="add-custom-term">+ Add custom payment term</Button>
          </div>
        )}
        <div>
          <Label className="text-sm font-medium text-zinc-700">Payment Date</Label>
          <Input type="date" className="mt-1.5 border-zinc-300" value={meta.payment_date} disabled={disabled} onChange={(e) => onMetaChange("payment_date", e.target.value)} />
        </div>
        <div>
          <Label className="text-sm font-medium text-zinc-700">Payment Method</Label>
          <Select value={meta.payment_method} onValueChange={(v) => onMetaChange("payment_method", v)} disabled={disabled}>
            <SelectTrigger className="mt-1.5 border-zinc-300"><SelectValue /></SelectTrigger>
            <SelectContent className="bg-white">{PAYMENT_METHODS.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div>
          <Label className="text-sm font-medium text-zinc-700">Total Initial Payment</Label>
          <div className="mt-1.5 h-10 flex items-center rounded-md border border-emerald-200 bg-emerald-50/60 px-3 text-sm font-bold text-emerald-700" data-testid="payment-terms-total">{inr(total)}</div>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-zinc-500">Leave a term blank to skip it — only the terms you fill in are recorded, as Paid payments against this customer.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------
// Customer Documents & Images section (multi-file picker with per-file status)
// ---------------------------------------------------------------------------------------------------------
function StatusPill({ item }) {
  if (item.status === "uploading") return <span className="inline-flex items-center gap-1 text-[11px] text-blue-600"><Loader2 className="h-3 w-3 animate-spin" />Uploading…</span>;
  if (item.status === "uploaded") return <span className="inline-flex items-center gap-1 text-[11px] text-emerald-700"><CheckCircle2 className="h-3 w-3" />Uploaded</span>;
  if (item.status === "error") return <span className="inline-flex items-center gap-1 text-[11px] text-red-600"><AlertCircle className="h-3 w-3" />Failed</span>;
  if (item.status === "invalid") return <span className="inline-flex items-center gap-1 text-[11px] text-red-600"><AlertCircle className="h-3 w-3" />Not accepted</span>;
  return <span className="text-[11px] text-zinc-500">Ready to upload</span>;
}

export function DocumentsSection({ items, onAdd, onRemove, onDocType, disabled, existingCount = 0 }) {
  const inputRef = useRef(null);
  // Release object URLs for image previews when an item goes away.
  useEffect(() => () => items.forEach((i) => i.previewUrl && URL.revokeObjectURL(i.previewUrl)), []); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (files.length) onAdd(files);
  };

  return (
    <div className="sm:col-span-2" data-testid="customer-documents-section">
      <div className="-mx-1 pt-2 pb-1.5 text-xs font-bold uppercase tracking-wide text-royal border-b border-zinc-200 mb-3">Customer Documents &amp; Images</div>
      <div className="flex flex-wrap items-center gap-3">
        <input ref={inputRef} type="file" multiple accept={DOC_ACCEPT} className="hidden" onChange={pick} data-testid="customer-documents-input" />
        <Button type="button" variant="outline" className="border-zinc-300" disabled={disabled} onClick={() => inputRef.current?.click()}>
          <Upload className="h-4 w-4 mr-1.5" />Upload Documents
        </Button>
        <span className="text-[11px] text-zinc-500">PDF, JPG, JPEG, PNG, WebP, DOC, DOCX, XLSX · up to 5 MB each · you can select several files at once.{existingCount ? ` ${existingCount} document(s) already saved for this customer are kept.` : ""}</span>
      </div>
      {items.length > 0 && (
        <ul className="mt-3 divide-y divide-zinc-100 rounded-lg border border-zinc-200" data-testid="customer-documents-list">
          {items.map((it) => (
            <li key={it.uid} className="flex flex-wrap items-center gap-3 px-3 py-2">
              <div className="h-10 w-10 shrink-0 rounded-md bg-zinc-100 flex items-center justify-center overflow-hidden">
                {it.previewUrl ? <img src={it.previewUrl} alt="" className="h-10 w-10 object-cover" /> : (isImageMime(resolveMime(it.file)) ? <ImageIcon className="h-5 w-5 text-zinc-400" /> : <FileText className="h-5 w-5 text-zinc-400" />)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-zinc-800 truncate" title={it.file.name}>{it.file.name}</div>
                <div className="text-[11px] text-zinc-500">{fileTypeLabel(it.file)} · {formatBytes(it.file.size)} · <StatusPill item={it} /></div>
                {it.error && <div className="text-[11px] text-red-600 mt-0.5" role="alert">{it.error}</div>}
              </div>
              {it.status !== "invalid" && it.status !== "uploaded" && (
                <Select value={it.docType} onValueChange={(v) => onDocType(it.uid, v)} disabled={disabled || it.status === "uploading"}>
                  <SelectTrigger className="h-8 w-[200px] border-zinc-300 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent className="bg-white">{DOC_TYPES.map((d) => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent>
                </Select>
              )}
              {it.status !== "uploaded" && (
                <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label={`Remove ${it.file.name}`} disabled={it.status === "uploading"} onClick={() => onRemove(it.uid)}>
                  <X className="h-4 w-4 text-zinc-500" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
