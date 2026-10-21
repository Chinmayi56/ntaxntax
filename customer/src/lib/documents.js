import api from "@/lib/api";

// Types a browser can render itself. Everything else (DOC/DOCX/XLSX) is downloaded.
const PREVIEWABLE = new Set(["application/pdf", "image/jpeg", "image/png", "image/webp"]);
const EXT_BY_MIME = {
  "application/pdf": "pdf",
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
  "application/msword": "doc",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
};

// Browsers block top-level navigation to data: URLs, so the stored base64 data URL is
// converted to a real Blob before it is opened or downloaded.
function dataUrlToBlob(dataUrl) {
  const text = String(dataUrl || "");
  const marker = text.indexOf(";base64,");
  if (!text.startsWith("data:") || marker === -1) throw new Error("The stored document data is not in a valid format.");
  const mime = text.slice(5, marker);
  const binary = atob(text.slice(marker + 8));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function resolveFileName(doc, fallbackName, mime) {
  const name = String(doc.file_name || fallbackName || doc.name || "").trim();
  if (name && /\.[A-Za-z0-9]{2,5}$/.test(name)) return name;
  const ext = EXT_BY_MIME[mime];
  return `${name || "document"}${ext ? `.${ext}` : ""}`;
}

function saveBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// GET /api/documents/{id} -> data.data.file_data (real MongoDB data, authorized by the backend).
export async function fetchFullDocument(id) {
  const { data } = await api.get(`/documents/${encodeURIComponent(id)}`);
  const full = data?.data;
  if (!full?.file_data) throw new Error("This document has no stored file content.");
  return full;
}

export async function previewDocument(row) {
  // Open the tab synchronously (inside the click) so popup blockers allow it, then navigate it.
  const tab = window.open("", "_blank");
  try {
    const full = await fetchFullDocument(row.id);
    const blob = dataUrlToBlob(full.file_data);
    const mime = blob.type || full.mime_type;
    const fileName = resolveFileName(full, row.name, mime);
    if (!PREVIEWABLE.has(mime)) {
      if (tab) tab.close();
      saveBlob(blob, fileName); // DOC/DOCX/XLSX cannot be shown by the browser: download instead.
      return "downloaded";
    }
    if (!tab) throw new Error("Please allow pop-ups for this site to preview the document.");
    tab.opener = null;
    tab.location.href = URL.createObjectURL(blob);
    return "previewed";
  } catch (e) {
    if (tab) tab.close();
    throw e;
  }
}

export async function downloadDocument(row) {
  const full = await fetchFullDocument(row.id);
  const blob = dataUrlToBlob(full.file_data);
  saveBlob(blob, resolveFileName(full, row.name, blob.type || full.mime_type));
}
