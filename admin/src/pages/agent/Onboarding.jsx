
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import PageHeader from "@/components/shared/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { UserPlus } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";

const TYPES = [
  "Private Limited",
  "Public Limited",
  "Proprietorship",
  "Partnership",
  "LLP",
];

const STATES = [
  "Telangana",
  "Andhra Pradesh",
  "Karnataka",
  "Tamil Nadu",
  "Maharashtra",
];


const FIELDS = [
  {
    key: "business_name",
    label: "Business Name",
    full: true,
  },
  {
    key: "owner",
    label: "Contact Person",
  },
  {
    key: "mobile",
    label: "Mobile",
  },
  {
    key: "email",
    label: "Email",
  },
  {
    key: "gst_number",
    label: "GST Number",
  },
  {
    key: "pan",
    label: "PAN",
  },
  {
    key: "tan",
    label: "TAN",
  },
  {
    key: "cin",
    label: "CIN",
  },
  {
    key: "business_type",
    label: "Business Type",
    type: "select",
    options: TYPES,
  },
  {
    key: "state",
    label: "State",
    type: "select",
    options: STATES,
  },
  {
    key: "city",
    label: "City",
  },
  {
    key: "assigned_employee",
    label: "Assigned Consultant",
    type: "select",
    options: [],
  },
];

export default function Onboarding() {
  const { user } = useAuth();
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [employees, setEmployees] = useState([]);
  useEffect(() => {
    let active = true;
    api.get("/employees", { params: { page: 1, page_size: 500 } })
      .then(({ data }) => {
        if (active) setEmployees(Array.isArray(data?.data) ? data.data.filter((x) => x?.id && String(x.status || "Active").toLowerCase() !== "inactive") : []);
      })
      .catch(() => {});
    return () => { active = false; };
  }, []);
  const navigate = useNavigate();

  const submit = async () => {
    if (!form.business_name?.trim()) {
      toast.error("Business Name is required");
      return;
    }

    setSaving(true);

    try {
      const employee = employees.find((x) => x.id === form.assigned_employee);
      await api.post("/customers", {
        ...form,
        status: "Active",
        assigned_agent: user?.name || "",
        agent_id: user?.meta?.agent_id || undefined,
        assigned_employee: employee?.name || "",
        employee_id: employee?.id || undefined,
        outstanding: 0,
      });

      toast.success("Customer onboarded successfully!");
      navigate("/agent/customers");
    } catch (e) {
      console.error("Customer onboarding failed:", e);
      toast.error("Onboarding failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Customer Onboarding"
        breadcrumb={["Tax Consultant", "Onboarding"]}
        subtitle="Convert a lead into a registered customer."
      />

      <div className="bg-white border border-zinc-200 rounded-xl shadow-sm p-6 max-w-3xl">
        <div className="flex items-center gap-3 mb-6">
          <div className="h-10 w-10 rounded-lg bg-brand-faint flex items-center justify-center text-brand-hover">
            <UserPlus className="h-5 w-5" />
          </div>

          <h3 className="font-heading text-base font-semibold text-zinc-900">
            New Customer Details
          </h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {FIELDS.map((f) => (
            <div
              key={f.key}
              className={f.full ? "sm:col-span-2" : ""}
            >
              <Label className="text-sm font-medium text-zinc-700">
                {f.label}
              </Label>

              {f.type === "select" ? (
                <Select
                  value={form[f.key] || ""}
                  onValueChange={(v) =>
                    setForm((s) => ({
                      ...s,
                      [f.key]: v,
                    }))
                  }
                >
                  <SelectTrigger
                    className="mt-1.5 border-zinc-300"
                    data-testid={`onb-${f.key}`}
                  >
                    <SelectValue
                      placeholder={`Select ${f.label}`}
                    />
                  </SelectTrigger>

                  <SelectContent className="bg-white">
                    {(f.key === "assigned_employee"
                      ? employees.map((emp) => ({ value: emp.id, label: emp.name || emp.id }))
                      : (f.options || []).map((value) => ({ value, label: value }))
                    ).map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input
                  value={form[f.key] || ""}
                  onChange={(e) =>
                    setForm((s) => ({
                      ...s,
                      [f.key]: e.target.value,
                    }))
                  }
                  className="mt-1.5 border-zinc-300 focus-visible:ring-brand/30"
                  data-testid={`onb-${f.key}`}
                />
              )}
            </div>
          ))}
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <Button
            variant="outline"
            className="border-zinc-300"
            onClick={() => setForm({})}
          >
            Reset
          </Button>

          <Button
            className="bg-brand text-zinc-900 hover:bg-brand-hover font-semibold"
            onClick={submit}
            disabled={saving}
            data-testid="onboard-submit"
          >
            <UserPlus className="h-4 w-4 mr-1.5" />
            {saving ? "Onboarding..." : "Onboard Customer"}
          </Button>
        </div>
      </div>
    </div>
  );
}

