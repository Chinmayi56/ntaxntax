import { useState, useEffect, useCallback } from "react";
import { toast } from "sonner";
import api, { describeApiError } from "@/lib/api";
import { notifyDataChanged } from "@/lib/dataSync";

const RELATED_RESOURCES = {
  payments: ["payments", "invoices", "bookings", "customers", "agents"],
  invoices: ["invoices", "payments", "bookings", "customers"],
  bookings: ["bookings", "customers", "agents", "invoices"],
  customers: ["customers", "bookings", "invoices", "payments"],
  agents: ["agents", "bookings", "commissions", "payments"],
  services: ["services", "bookings", "invoices", "payments", "customers"],
};


export function useCrud(name, { pollMs = 0, fetchAll = false, enabled = true } = {}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (opts) => {
    const silent = opts && opts.silent === true;
    if (!enabled) { setLoading(false); return; }
    if (!silent) setLoading(true);
    try {
      // fetchAll: follow the backend pagination so the list holds every record (the default page is
      // 100 rows) and therefore always agrees with server-side totals such as the customer summary.
      const { data } = await api.get(`/${name}`, fetchAll ? { params: { page: 1, page_size: 500 } } : undefined);
      let items = Array.isArray(data?.data) ? data.data : Array.isArray(data) ? data : [];
      const pages = Number(data?.pagination?.pages || 1);
      if (fetchAll && pages > 1) {
        for (let p = 2; p <= Math.min(pages, 200); p += 1) {
          const next = await api.get(`/${name}`, { params: { page: p, page_size: 500 } });
          items = items.concat(Array.isArray(next.data?.data) ? next.data.data : []);
        }
      }
      setRows(items);
    } catch (e) {
      if (!silent) toast.error(describeApiError(e, `Failed to load ${name}`));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [name, fetchAll, enabled]);

  useEffect(() => {
    load();
    const handler = (event) => {
      const resources = event?.detail?.resources || [];
      if (event?.detail?.source === name) return;
      if (resources.includes(name) || resources.includes("*")) load();
    };
    window.addEventListener("ntaxco:data-changed", handler);
    // Reconcile stale data whenever the user returns to this module/tab, even when
    // periodic polling is disabled. Optional polling remains opt-in per module.
    let timer = null;
    const onVisible = () => { if (document.visibilityState === "visible") load({ silent: true }); };
    document.addEventListener("visibilitychange", onVisible);
    if (pollMs > 0) timer = window.setInterval(() => { if (!document.hidden) load({ silent: true }); }, pollMs);
    return () => {
      window.removeEventListener("ntaxco:data-changed", handler);
      document.removeEventListener("visibilitychange", onVisible);
      if (timer) window.clearInterval(timer);
    };
  }, [load, name, pollMs]);

  const create = async (body) => {
    const { data } = await api.post(`/${name}`, body);
    await load();
    toast.success("Record created");
    notifyDataChanged(RELATED_RESOURCES[name] || [name], name);
    return data?.data;
  };

  const update = async (id, body) => {
    const { data } = await api.put(`/${name}/${id}`, body);
    await load();
    toast.success("Record updated");
    notifyDataChanged(RELATED_RESOURCES[name] || [name], name);
    return data?.data;
  };

  const remove = async (id) => {
    try {
      const { data } = await api.delete(`/${name}/${id}`);
      await load();
      toast.success(name === "customers" ? "Customer removed from active management; history retained" : "Record deleted");
      notifyDataChanged(RELATED_RESOURCES[name] || [name], name);
      return data?.data;
    } catch (e) {
      // The backend enforces service/financial eligibility and preserves historical
      // customer records through soft deletion; callers must surface any rejection.
      toast.error(describeApiError(e, `Failed to delete ${name.slice(0, -1)}`));
      throw e;
    }
  };

  return { rows, loading, load, create, update, remove };
}
