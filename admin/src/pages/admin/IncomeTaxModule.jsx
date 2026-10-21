import CrudModule from "@/components/shared/CrudModule";
import ModuleKanban from "@/components/shared/ModuleKanban";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { BadgeIndianRupee, CheckCircle2, Clock, CalendarClock } from "lucide-react";

const RETURNS = ["ITR-3", "ITR-4", "ITR-5", "ITR-6"];
const STATUS = ["Pending", "Running", "Completed"];

export default function IncomeTaxModule() {
  return (
    <CrudModule
      customerService="Income Tax" showAllDetailFields
      title="Income Tax Returns" singular="ITR" name="itr" breadcrumb={["Super Admin", "Income Tax"]}
      extra={(_rows, _loading, _load, ctx) => <ModuleKanban board="itr" resource="itr" title="Income Tax" showHeading={false} showToolbar={false} onOpenRecord={ctx?.openRecord} refreshKey={ctx?.refreshKey} searchFields={["client", "company", "customer", "customer_name", "business_name", "owner", "gstin", "pan", "form", "return_type", "return_no", "consultant"]} />}
      kpiFn={(r) => [
        { title: "Total ITRs", value: r.length, icon: BadgeIndianRupee },
        { title: "Filed", value: r.filter((x) => x.status === "Completed").length, icon: CheckCircle2 },
        { title: "Pending", value: r.filter((x) => x.status === "Pending").length, icon: Clock },
        { title: "Upcoming", value: r.filter((x) => x.status === "Running").length, icon: CalendarClock },
      ]}
      columns={[
        { key: "client", label: "Client" },
        { key: "ay", label: "Assessment Yr" },
        { key: "pan", label: "PAN" },
        { key: "return_no", label: "Return" },
        { key: "due_date", label: "Due Date" },
        { key: "filed_date", label: "Filed", render: (r) => r.filed_date || "—" },
        { key: "consultant", label: "Consultant" },
        { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
      ]}
      fields={[
        { key: "client", label: "Client", required: true },
        { key: "ay", label: "Assessment Year", default: "2026-27" },
        { key: "pan", label: "PAN" },
        { key: "return_no", label: "Return Type", type: "select", options: RETURNS },
        { key: "due_date", label: "Due Date", type: "date" },
        { key: "filed_date", label: "Filed Date", type: "date" },
        { key: "ack", label: "Acknowledgement No" },
        { key: "consultant", label: "Consultant" },
        { key: "status", label: "Status", type: "select", options: STATUS, default: "Pending" },
      ]}
    />
  );
}
