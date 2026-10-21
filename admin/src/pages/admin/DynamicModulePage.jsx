import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import api, { describeApiError } from "@/lib/api";
import DynamicBoard from "@/components/shared/DynamicBoard";
import PageHeader from "@/components/shared/PageHeader";
import EmptyState from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { Boxes, Settings } from "lucide-react";

// Renders ANY admin-created "Modules / Dynamic Configuration" module —
// nothing here is hard-coded to a specific module. The module's fields,
// labels and options are all read from its saved configuration, and the
// board (lists + cards + drag-and-drop) is powered by the generic
// /dynamic-records/{module_key} and /dynamic-board/{module_key} backend APIs.
export default function DynamicModulePage() {
  const { moduleSlug } = useParams();
  const [status, setStatus] = useState("loading"); // loading | found | missing | error
  const [config, setConfig] = useState(null);

  useEffect(() => {
    let alive = true;
    setStatus("loading");
    api.get("/admin/dynamic-config")
      .then(({ data }) => {
        if (!alive) return;
        const rows = Array.isArray(data) ? data : Array.isArray(data?.data) ? data.data : [];
        const match = rows.find((c) => c?.key === moduleSlug);
        if (match) { setConfig(match); setStatus("found"); }
        else setStatus("missing");
      })
      .catch((e) => {
        if (!alive) return;
        toast.error(describeApiError(e, "Unable to load this module"));
        setStatus("error");
      });
    return () => { alive = false; };
  }, [moduleSlug]);

  if (status === "loading") {
    return (
      <div>
        <PageHeader title="Loading module..." breadcrumb={["Super Admin", "Modules"]} />
        <div className="bg-white border border-zinc-200 rounded-xl shadow-sm p-6 space-y-3">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-2/3" />
        </div>
      </div>
    );
  }

  if (status === "missing" || status === "error") {
    return (
      <div>
        <PageHeader title="Module not found" breadcrumb={["Super Admin", "Modules"]} />
        <EmptyState
          icon={Boxes}
          title={status === "missing" ? "This module doesn't exist (yet)" : "Couldn't load this module"}
          description={
            status === "missing"
              ? "It may have been deleted, or the link is out of date. Create or manage modules from Settings → Modules / Dynamic Configuration."
              : "Something went wrong while loading this module. Please try again."
          }
          action={{ label: "Go to Settings", onClick: () => { window.location.href = "/admin/settings"; } }}
        />
      </div>
    );
  }

  if (config.enabled === false) {
    return (
      <div>
        <PageHeader title={config.name} breadcrumb={["Super Admin", config.name]} />
        <EmptyState
          icon={Settings}
          title={`${config.name} is disabled`}
          description="Enable this module from Settings → Modules / Dynamic Configuration to start adding records."
          action={{ label: "Go to Settings", onClick: () => { window.location.href = "/admin/settings"; } }}
        />
      </div>
    );
  }

  // MODULE = BOARD. Lists are columns, records are cards, and the module's
  // configured fields (unchanged field system) define each card's data.
  return (
    <DynamicBoard
      key={config.key}
      config={config}
      breadcrumb={["Super Admin", "Modules", config.name]}
    />
  );
}
