import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useNavigate, useLocation } from "react-router-dom";
import { Menu, X, Bell, LogOut, Search, ChevronDown, Check, LayoutGrid, Users, UserRound, BriefcaseBusiness, CalendarDays, Wrench, FolderKanban, ClipboardList, Receipt, CreditCard, FileText, Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { NAV, ROLES } from "@/lib/constants";
import { useDynamicModules } from "@/hooks/useDynamicModules";
import Logo from "@/components/shared/Logo";
import NotificationBell from "@/components/shared/NotificationBell";
import { useNotificationCount } from "@/hooks/useNotificationCount";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator, DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { toast } from "sonner";
import api, { describeApiError } from "@/lib/api";
import AiTaxCopilot from "@/components/shared/AiTaxCopilot";



const SEARCH_ICONS = {
  customer: Users,
  employee: UserRound,
  agent: BriefcaseBusiness,
  booking: CalendarDays,
  service: Wrench,
  project: FolderKanban,
  task: ClipboardList,
  invoice: Receipt,
  payment: CreditCard,
  document: FileText,
  gst: FileText,
  income_tax: FileText,
  tds: FileText,
  roc: FileText,
};

function GlobalAdminSearch({ role }) {
  const navigate = useNavigate();
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState([]);
  const [activeIndex, setActiveIndex] = useState(-1);

  const flatResults = useMemo(() => results.flatMap((group) => group.results || []), [results]);

  useEffect(() => {
    if (role !== "admin") return undefined;
    const value = query.trim();
    if (value.length < 2) {
      setResults([]);
      setError("");
      setLoading(false);
      setActiveIndex(-1);
      return undefined;
    }
    setOpen(true);
    setLoading(true);
    setError("");
    const timer = window.setTimeout(async () => {
      try {
        const { data } = await api.get("/admin/search", { params: { q: value } });
        const groups = Array.isArray(data?.results)
          ? data.results.filter((group) => Array.isArray(group?.results) && group.results.length)
          : [];
        setResults(groups);
        setActiveIndex(groups.length ? 0 : -1);
      } catch (err) {
        setResults([]);
        setActiveIndex(-1);
        setError(describeApiError(err, "Unable to search right now. Please try again."));
      } finally {
        setLoading(false);
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, role]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  const openResult = (result) => {
    if (!result?.route) return;
    setOpen(false);
    setQuery("");
    setResults([]);
    navigate(result.route);
  };

  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (!open || !flatResults.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % flatResults.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index <= 0 ? flatResults.length - 1 : index - 1));
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      openResult(flatResults[activeIndex]);
    }
  };

  if (role !== "admin") return null;

  let flatOffset = 0;
  return (
    <div ref={rootRef} className="relative flex-1 min-w-0 max-w-[20rem] md:w-72 md:flex-none">
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400 z-10" />
      <Input
        ref={inputRef}
        value={query}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder="Search anything..."
        className="pl-9 pr-9 bg-zinc-50 border-zinc-200 focus-visible:ring-brand/30"
        data-testid="global-search"
        autoComplete="off"
        aria-label="Search anything"
        aria-expanded={open}
        aria-controls="admin-global-search-results"
      />
      {loading && <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 animate-spin text-zinc-400" />}
      {open && query.trim().length > 0 && (
        <div id="admin-global-search-results" role="listbox" className="absolute top-[calc(100%+0.5rem)] left-0 w-[min(34rem,calc(100vw-2rem))] max-h-[min(70vh,34rem)] overflow-y-auto rounded-xl border border-zinc-200 bg-white shadow-xl z-50">
          {query.trim().length < 2 ? (
            <div className="px-4 py-3 text-sm text-zinc-500">Type at least 2 characters</div>
          ) : loading ? (
            <div className="flex items-center gap-2 px-4 py-4 text-sm text-zinc-500"><Loader2 className="h-4 w-4 animate-spin" />Searching...</div>
          ) : error ? (
            <div className="px-4 py-4 text-sm text-red-600">{error}</div>
          ) : !flatResults.length ? (
            <div className="px-4 py-4 text-sm text-zinc-500">
              <p className="font-medium text-zinc-700">No results found</p>
              <p className="mt-1">Try another name, ID, mobile number, invoice number, or task.</p>
            </div>
          ) : (
            <div className="py-2">
              {results.map((group) => {
                const GroupIcon = SEARCH_ICONS[group.type] || FileText;
                const startIndex = flatOffset;
                flatOffset += group.results.length;
                return (
                  <div key={group.type} className="py-1">
                    <div className="px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">{group.label}</div>
                    {group.results.map((result, index) => {
                      const itemIndex = startIndex + index;
                      const Icon = SEARCH_ICONS[result.type] || GroupIcon;
                      return (
                        <button
                          key={`${result.type}-${result.id}-${itemIndex}`}
                          type="button"
                          role="option"
                          aria-selected={itemIndex === activeIndex}
                          onMouseEnter={() => setActiveIndex(itemIndex)}
                          onClick={() => openResult(result)}
                          className={`w-full text-left px-4 py-2.5 flex items-start gap-3 transition-colors ${itemIndex === activeIndex ? "bg-zinc-50" : "hover:bg-zinc-50"}`}
                        >
                          <span className="mt-0.5 h-8 w-8 shrink-0 rounded-lg bg-zinc-100 flex items-center justify-center"><Icon className="h-4 w-4 text-zinc-600" /></span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm font-medium text-zinc-800 truncate">{result.title}</span>
                            <span className="block text-xs text-zinc-500 truncate">{result.subtitle}</span>
                            {result.meta && <span className="block text-xs text-zinc-400 truncate">{result.meta}</span>}
                          </span>
                          {result.status && <span className="shrink-0 text-[11px] font-medium text-zinc-500">{result.status}</span>}
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SidebarNav({ role, onNavigate }) {
  const location = useLocation();
  const dynamicModules = useDynamicModules(role === "admin", location.pathname);
  const unreadNotifications = useNotificationCount();

  const baseItems = NAV[role] || [];
  let items = baseItems;
  if (role === "admin" && dynamicModules.length > 0) {
    // Admin-created modules (Settings → Modules / Dynamic Configuration)
    // are inserted just above "Settings" so they read as first-class ERP
    // modules rather than an afterthought at the very bottom.
    const dynamicItems = dynamicModules.map((m) => ({
      label: m.name,
      path: `/admin/${m.key}`,
      icon: LayoutGrid,
      dynamic: true,
    }));
    const settingsIdx = baseItems.findIndex((it) => it.path.endsWith("/settings"));
    items = settingsIdx === -1
      ? [...baseItems, ...dynamicItems]
      : [...baseItems.slice(0, settingsIdx), ...dynamicItems, ...baseItems.slice(settingsIdx)];
  }

  return (
    <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
      {items.map((item) => (
        <NavLink
  key={item.path}
  to={item.path}
  onClick={onNavigate}
  data-testid={`nav-${item.label.toLowerCase().replace(/\s+/g, "-")}`}
  className={({ isActive }) =>
    `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.99] ${
      isActive
        ? "bg-royal-faint text-royal"
        : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900"
    }`
  }
>
  <item.icon className="h-[18px] w-[18px]" strokeWidth={2} />
  <span>{item.label}</span>
  {item.path.endsWith("/notifications") && unreadNotifications > 0 && (
    <span className="ml-auto min-w-[20px] h-5 px-1.5 rounded-full bg-royal text-white text-[11px] font-bold flex items-center justify-center" data-testid="nav-notifications-count">{unreadNotifications > 99 ? "99+" : unreadNotifications}</span>
  )}
</NavLink>
      ))}
    </nav>
  );
}

export default function PortalLayout({ role }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const roleInfo = ROLES[role];

  const handleLogout = async () => {
    await logout();
    toast.success("Logged out successfully");
    navigate(`/login/${role}`);
  };

  const initials = (user?.name || "U").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="min-h-screen flex bg-background mobile-responsive" data-portal-role={role}>
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex w-64 flex-col bg-white border-r border-zinc-200 fixed inset-y-0 left-0 z-30">
        <div className="h-16 flex items-center px-5 border-b border-zinc-100">
          <Logo className="h-11" />
        </div>
        <SidebarNav role={role} />
        <div className="p-3 border-t border-zinc-100">
          <div className="px-3 py-2 rounded-lg bg-zinc-50">
            <p className="text-xs text-zinc-400 uppercase tracking-wider">{roleInfo.label}</p>
            <p className="text-sm font-medium text-zinc-700 truncate">{user?.name}</p>
          </div>
        </div>
      </aside>

      <div className="flex-1 lg:ml-64 flex flex-col min-w-0">
        {/* Navbar */}
        <header className="h-16 bg-white border-b border-zinc-200 sticky top-0 z-20 flex items-center justify-between px-4 sm:px-6 gap-4">
          <div className="flex items-center gap-3">
            <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
              <SheetTrigger asChild>
                <Button variant="ghost" size="icon" className="lg:hidden" data-testid="mobile-menu-btn"><Menu className="h-5 w-5" /></Button>
              </SheetTrigger>
              <SheetContent side="left" className="p-0 w-72 bg-white">
                <div className="h-16 flex items-center px-5 border-b border-zinc-100"><Logo className="h-10" /></div>
                <div className="flex flex-col h-[calc(100%-4rem)]">
                  <SidebarNav role={role} onNavigate={() => setMobileOpen(false)} />
                </div>
              </SheetContent>
            </Sheet>
            <GlobalAdminSearch role={role} />
          </div>

          <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="relative" data-testid="notification-bell-old-hidden" style={{ display: "none" }} />
              </DropdownMenuTrigger>
            </DropdownMenu>
            <NotificationBell base={roleInfo.base} />

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex items-center gap-2 pl-1 pr-2 py-1 rounded-lg hover:bg-zinc-100 transition-colors" data-testid="user-menu">
                  <Avatar className="h-8 w-8"><AvatarFallback className="bg-brand text-zinc-900 text-xs font-semibold">{initials}</AvatarFallback></Avatar>
                  <span className="hidden sm:block text-sm font-medium text-zinc-700 max-w-[120px] truncate">{user?.name}</span>
                  <ChevronDown className="h-4 w-4 text-zinc-400 hidden sm:block" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56 bg-white">
                <DropdownMenuLabel>
                  <p className="text-sm font-medium text-zinc-800">{user?.name}</p>
                  <p className="text-xs text-muted-foreground font-normal">{user?.email || user?.mobile}</p>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="cursor-pointer" onClick={() => navigate(`${roleInfo.base}/dashboard`)}>Dashboard</DropdownMenuItem>
                <DropdownMenuItem className="cursor-pointer text-red-600 focus:text-red-600" onClick={handleLogout} data-testid="logout-btn">
                  <LogOut className="h-4 w-4 mr-2" />Logout
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <main className="flex-1 p-4 sm:p-6 lg:p-8 min-w-0">
          <div key={location.pathname} className="page-enter">
            <Outlet />
          </div>
        </main>
      </div>
      {(role === "admin" || role === "agent") && <AiTaxCopilot role={role} />}
    </div>
  );
}
