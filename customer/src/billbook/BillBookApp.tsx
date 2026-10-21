// @ts-nocheck
import React,{useCallback,useEffect,useMemo,useRef,useState} from 'react';

import {Routes,Route,Navigate as RouterNavigate,useLocation as useRouterLocation,useNavigate as useRouterNavigate} from 'react-router-dom';
import * as Icons from 'lucide-react';
import {AreaChart,Area,BarChart,Bar,LineChart,Line,PieChart,Pie,Cell,XAxis,YAxis,CartesianGrid,Tooltip,ResponsiveContainer,Legend} from 'recharts';
import {uid} from './services/storage';

import {money,totals,buildGeneralLedger,buildJournals,today} from './services/calculations';
import type {UserData,User,InvoiceItem,Product,Customer,Payment,Expense,Role,BackupData} from './types';
import {createDemoData} from './data/mock';
import './index.css';

const BILLBOOK_API_BASE=String(process.env.REACT_APP_API_URL||'/api').replace(/\/+$/, '');

// My BillBook shares NTAXCO's BrowserRouter. Keep its internal URLs relative to /my-billbook.
function useLocation(){const location=useRouterLocation();const prefix='/my-billbook';return {...location,pathname:location.pathname.startsWith(prefix)?(location.pathname.slice(prefix.length)||'/'):location.pathname};}
function useNavigate(){const navigate=useRouterNavigate();return (to:any,options?:any)=>{if(typeof to==='string'&&to.startsWith('/'))return navigate(to==='/my-billbook'||to.startsWith('/my-billbook/')?to:`/my-billbook${to==='/'?'':to}`,options);return navigate(to,options);};}
function BillBookNavigate({to,...props}:any){const target=typeof to==='string'&&to.startsWith('/')?(to==='/my-billbook'||to.startsWith('/my-billbook/')?to:`/my-billbook${to==='/'?'':to}`):to;return <RouterNavigate to={target} {...props}/>;}
type Save=(d:UserData)=>void;
const clone=<T,>(v:T):T=>JSON.parse(JSON.stringify(v));
const moduleColors:Record<string,string>={Dashboard:'#6366f1',Sales:'#3b82f6',Purchases:'#f59e0b',Inventory:'#22c55e',Parties:'#8b5cf6',Payments:'#14b8a6',Expenses:'#f97316',Accounting:'#8b5cf6',GST:'#f59e0b',Reports:'#38bdf8',Utilities:'#eab308',Notifications:'#f43f5e',Settings:'#64748b',Profile:'#a78bfa'};

const nav = [
  ['Dashboard', '/', Icons.LayoutDashboard],

  ['Sales', null, Icons.Receipt, [
    ['POS Billing', '/sales/pos'],
    ['Invoices', '/sales/invoices'],
    ['Sales Orders', '/sales/orders'],
    ['Sales Returns', '/sales/returns'],
    ['Payments Received', '/sales/payments'],
  ]],

  ['Purchases', null, Icons.ShoppingCart, [
    ['Purchase Bills', '/purchases/bills'],
    ['Payments Made', '/purchases/payments'],
  ]],

  ['Inventory', null, Icons.Package, [
    ['Products', '/inventory/products'],
    ['Categories', '/inventory/categories'],
    ['Stock', '/inventory/stock'],
    ['Stock Adjustment', '/inventory/adjustment'],
    ['Low Stock', '/inventory/low-stock'],
    ['Batch & Expiry', '/inventory/batches'],
    ['Barcode', '/inventory/barcode'],
    ['Stock Transfer', '/inventory/transfers'],
  ]],

  ['Parties', null, Icons.Users, [
    ['Customers', '/parties/customers'],
    ['Suppliers', '/parties/suppliers'],
    ['Customer Ledger', '/parties/customer-ledger'],
    ['Supplier Ledger', '/parties/supplier-ledger'],
  ]],

  ['Payments', null, Icons.WalletCards, [
    ['Payment History', '/payments/history'],
    ['Payment Reminders', '/payments/reminders'],
  ]],

  ['Accounting', null, Icons.Calculator, [
    ['General Ledger', '/accounting/general-ledger'],
    ['Trial Balance', '/accounting/trial-balance'],
  ]],

  ['GST', null, Icons.FileText, [
    ['Sales GST', '/gst/sales'],
  ]],

  ['Reports', null, Icons.BarChart3, [
    ['Sales', '/reports/sales'],
    ['Stock', '/reports/stock'],
    ['Receivables', '/reports/receivables'],
    ['Customers', '/reports/customers'],
    ['Suppliers', '/reports/suppliers'],
  ]],

  ['Utilities', null, Icons.Wrench, [
    ['Calendar', '/utilities/calendar'],
    ['Calculator', '/utilities/calculator'],
  ]],

  ['Notifications', '/notifications', Icons.Bell],

  ['My Drive', '/drive', Icons.Cloud],

  ['Settings', null, Icons.Settings, [
    ['Business Profile', '/settings/business'],
    ['Invoice Customization', '/settings/invoice'],
    ['Users & Permissions', '/settings/users'],
    ['Payment Settings', '/settings/payments'],
    ['Barcode Settings', '/settings/barcode'],
    ['Backup / Restore', '/settings/backup'],
    ['Activity / Audit Log', '/settings/audit'],
    ['Printer Settings', '/settings/printer'],
  ]],

  ['Profile', null, Icons.UserCircle, [
    ['My Profile', '/profile'],
    ['Login History', '/profile/history'],
    ['Logout', '/logout'],
  ]],
] as const;


function emptyData(user:User):UserData{const d=createDemoData(user);for(const k of ['customers','suppliers','products','invoices','payments','expenses','purchases','quotations','salesOrders','returns','ledgers','generalLedger','journalEntries','salesLedger','reminders','batches','barcodeData','heldBills','posBills','auditLogs','notifications','otherIncome','creditNotes','debitNotes','calendarEvents','recurringInvoices','expenseClaims'] as (keyof UserData)[]){(d[k] as any)=[]}d.bankBalance=0;d.cashBalance=0;d.categories=[];d.warehouses=[];d.businessProfile={...d.businessProfile,businessName:user.name,ownerName:user.name,mobile:user.mobile,email:'',address:'',gstin:''};d.invoiceSettings={...d.invoiceSettings,businessName:user.name,phone:user.mobile,email:'',address:'',gstin:'',bankDetails:'',upiId:'',upiQr:'',signature:''};return d}
function logoutNtaxco(){const token=localStorage.getItem('ntaxco_access_token')||'';void fetch(`${BILLBOOK_API_BASE}/auth/logout`,{method:'POST',headers:{Authorization:`Bearer ${token}`}}).catch(()=>{}).finally(()=>{for(const k of ['ntaxco_access_token','ntaxco_refresh_token','ntaxco_user','ntaxco_auth_mode'])localStorage.removeItem(k);window.location.href='/login?portal=billbook';});}
function ntaxcoUser():User|null{try{const raw=localStorage.getItem('ntaxco_user');const token=localStorage.getItem('ntaxco_access_token');if(!raw||!token)return null;const u=JSON.parse(raw);if(u?.role!=='customer'||!u?.id)return null;const now=new Date().toISOString();return{id:String(u.id),name:String(u.name||u.full_name||'NTAXCO Customer'),mobile:String(u.mobile||u.phone||''),role:'Business Owner',createdAt:now,lastLoginAt:now,loginHistory:[]}}catch{return null}}
function useData(){const [user,setUser]=useState<User|null>(()=>ntaxcoUser());const [data,setData]=useState<UserData|null>(null);const [loading,setLoading]=useState(true);const [error,setError]=useState('');const saveQueue=useRef(Promise.resolve());const refresh=async()=>{const u=ntaxcoUser();setUser(u);if(!u){setData(null);setLoading(false);return}setLoading(true);setError('');try{const r=await fetch(`${BILLBOOK_API_BASE}/billbook/data`,{headers:{Authorization:`Bearer ${localStorage.getItem('ntaxco_access_token')||''}`}});if(!r.ok){if(r.status===401){window.location.href='/login?portal=billbook';return}if(r.status===403){const x=await r.json().catch(()=>({}));if(x?.detail?.code==='subscription_required'){window.location.href='/billbook/plans?reason=subscription';return}}throw new Error(`Unable to load My BillBook data (${r.status}).`)}const payload=await r.json();const defaults=emptyData(u);const received=payload?.data&&typeof payload.data==='object'?payload.data:{};const next={...defaults,...received};for(const key of ['customers','suppliers','products','invoices','payments','expenses','purchases','quotations','salesOrders','returns','ledgers','generalLedger','journalEntries','salesLedger','reminders','batches','barcodeData','heldBills','posBills','auditLogs','notifications','otherIncome','creditNotes','debitNotes','calendarEvents','recurringInvoices','expenseClaims','categories','warehouses']){if(!Array.isArray((next as any)[key]))(next as any)[key]=[];}next.businessProfile={...defaults.businessProfile,...(received.businessProfile||{}),businessName:received.businessProfile?.businessName||u.name,ownerName:received.businessProfile?.ownerName||u.name,mobile:u.mobile};next.invoiceSettings={...defaults.invoiceSettings,...(received.invoiceSettings||{}),businessName:received.invoiceSettings?.businessName||u.name,phone:u.mobile};setData(next)}catch(e:any){setError(e?.message||'Unable to connect to My BillBook.');}finally{setLoading(false)}};useEffect(()=>{refresh()},[]);const save=(d:UserData)=>{if(!user)return;setData(d);setError('');const token=localStorage.getItem('ntaxco_access_token')||'';const body=JSON.stringify({data:d});saveQueue.current=saveQueue.current.catch(()=>{}).then(async()=>{const r=await fetch(`${BILLBOOK_API_BASE}/billbook/data`,{method:'PUT',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body});if(r.status===401){window.location.href='/login?portal=billbook';return}if(!r.ok){const x=await r.json().catch(()=>({}));if(r.status===403&&x?.detail?.code==='subscription_required'){window.location.href='/billbook/plans?reason=subscription';return}throw new Error(typeof x.detail==='string'?x.detail:(x.detail?.message||'Save failed. Please retry.'))}}).catch(e=>{setError(e?.message||'Unable to save My BillBook data.');});};return{user,data,save,refresh,loading,error}}
function App(){const {user,data,save,refresh,loading,error}=useData();const location=useLocation();const navigate=useNavigate();useEffect(()=>{const prefix='/my-billbook';const target=prefix+(location.pathname==='/'?'':location.pathname);if(window.location.pathname!==target)window.history.pushState({},'',target);const onPop=()=>{const path=window.location.pathname.startsWith(prefix)?(window.location.pathname.slice(prefix.length)||'/'):'/';navigate(path)};window.addEventListener('popstate',onPop);return()=>window.removeEventListener('popstate',onPop)},[location.pathname,navigate]);if(loading)return <div className="billbook-loading">Loading My BillBook…</div>;if(!user)return <div className="billbook-loading"><h2>NTAXCO customer sign-in required</h2><button className="btn primary" onClick={()=>window.location.href='/login?portal=billbook'}>Continue to NTAXCO login</button></div>;if(!data)return <div className="billbook-loading"><h2>My BillBook could not be loaded</h2><p>{error||'Please check your connection and try again.'}</p><button className="btn primary" onClick={()=>refresh()}>Retry</button><button className="btn" onClick={()=>window.location.href='/customer/dashboard'}>Back to NTAXCO</button></div>;return <div className="billbook-root">{error&&<div className="billbook-error" role="alert">{error} <button className="btn" onClick={()=>refresh()}>Reload</button></div>}<div className="billbook-return"><button className="btn" onClick={()=>window.location.href='/customer/dashboard'}>← Back to NTAXCO</button><span>My BillBook · Signed in through NTAXCO</span></div><Shell user={user} data={data} save={save}><Routes><Route path="/" element={<Dashboard data={data}/>}/><Route path="/sales/pos" element={<POS data={data} save={save}/>}/><Route path="/sales/invoices" element={<Invoices data={data} save={save}/>}/><Route path="/sales/orders" element={<SalesOrders data={data} save={save}/>}/><Route path="/parties/customers" element={<Customers data={data} save={save}/>}/><Route path="/parties/suppliers" element={<Suppliers data={data} save={save}/>}/><Route path="/inventory/products" element={<Products data={data} save={save}/>}/><Route path="/inventory/batches" element={<Batches data={data}/>}/><Route path="/inventory/barcode" element={<Barcode data={data} save={save}/>}/><Route path="/parties/customer-ledger" element={<Ledger data={data} customer/>}/><Route path="/parties/supplier-ledger" element={<Ledger data={data}/>}/><Route path="/payments/reminders" element={<Reminders data={data}/>}/><Route path="/utilities/calendar" element={<CalendarPage data={data} save={save}/>}/><Route path="/utilities/calculator" element={<Calculator/>}/><Route path="/settings/backup" element={<Backup data={data} save={save} userId={user.id}/>}/><Route path="/drive" element={<DriveSettings data={data} user={user} save={save}/>}/><Route path="/settings/drive" element={<BillBookNavigate to="/drive" replace/>}/><Route path="/settings/users" element={<Permissions data={data} save={save}/>}/><Route path="/settings/invoice" element={<SettingsPage title="Invoice Customization" data={data} save={save} section="invoice" />}/><Route path="/settings/business" element={<SettingsPage title="Business Profile" data={data} save={save} section="business" />}/><Route path="/settings/audit" element={<Audit data={data}/>}/><Route path="/profile" element={<Profile user={user}/>}/><Route path="/profile/history" element={<LoginHistory user={user}/>}/><Route path="/notifications" element={<Notifications data={data} save={save}/>}/><Route path="/store/*" element={<BillBookNavigate to="/" replace/>}/><Route path="/income/*" element={<BillBookNavigate to="/" replace/>}/><Route path="/banking/*" element={<BillBookNavigate to="/accounting/cash-book" replace/>}/><Route path="/accounting/customer-ledger" element={<BillBookNavigate to="/parties/customer-ledger" replace/>}/><Route path="/accounting/supplier-ledger" element={<BillBookNavigate to="/parties/supplier-ledger" replace/>}/><Route path="/reports/trial-balance" element={<BillBookNavigate to="/accounting/trial-balance" replace/>}/><Route path="/reports/general-ledger" element={<BillBookNavigate to="/accounting/general-ledger" replace/>}/><Route path="/accounting/general-ledger" element={<AccountingPage data={data} kind="General Ledger"/>}/><Route path="/accounting/trial-balance" element={<AccountingPage data={data} kind="Trial Balance"/>}/><Route path="/accounting/journal-entries" element={<AccountingPage data={data} kind="Journal Entries"/>}/><Route path="/accounting/cash-book" element={<AccountingPage data={data} kind="Cash Book"/>}/><Route path="/accounting/bank-book" element={<AccountingPage data={data} kind="Bank Book"/>}/><Route path="/expenses" element={<Expenses data={data} save={save}/>}/><Route path="/settings/profile" element={<Profile user={user}/>}/><Route path="*" element={<GenericPage data={data} save={save}/>}/></Routes></Shell></div>}

function Shell({user,data,save,children}:{user:User;data:UserData;save:Save;children:React.ReactNode}){const loc=useLocation(),navg=useNavigate();const [open,setOpen]=useState<Record<string,boolean>>({Sales:true,Inventory:true,Parties:true});const [collapsed,setCollapsed]=useState(false);const [mobile,setMobile]=useState(false);const [q,setQ]=useState('');const [create,setCreate]=useState(false);const results=useMemo(()=>{const z=q.toLowerCase().trim();if(!z)return [];const all=[...data.customers.map(x=>[x.name,'Customer','/parties/customers']),...data.suppliers.map(x=>[x.name,'Supplier','/parties/suppliers']),...data.products.flatMap(x=>[[x.name,'Product','/inventory/products'],[x.barcode,'Barcode','/inventory/barcode']]),...data.invoices.map(x=>[x.id,'Invoice','/sales/invoices']),...data.payments.map(x=>[x.id,'Payment','/payments/history']),...data.expenses.map(x=>[x.category,'Expense','/expenses']),...data.purchases.map(x=>[x.id,'Purchase Bill','/purchases/bills']),...data.quotations.map(x=>[x.id,'Quotation','/sales/quotations']),...data.salesOrders.map(x=>[x.id,'Sales Order','/sales/orders'])] as [string,string,string][];return all.filter(x=>x[0].toLowerCase().includes(z)).slice(0,10)},[q,data]);const active=nav.find(n=>n[1]===loc.pathname||n[3]?.some((c:any)=>c[1]===loc.pathname))?.[0]||'';return <div className="app"><aside className={`sidebar ${collapsed?'collapsed':''} ${mobile?'open':''}`}><div className="brand"><span className="brand-mark">BB</span><span className="hide-collapse">BillBooks</span></div><div className="nav-section">{nav.map((n:any)=>{const [label,to,I,children2]=n;const color=moduleColors[label];const isActive=to===loc.pathname||children2?.some((c:any)=>c[1]===loc.pathname);return <div key={label}><button className={`nav-item ${isActive?'active':''}`} style={{'--accent':color} as React.CSSProperties} onClick={()=>{if(to){navg(to);setMobile(false)}else setOpen(o=>({...o,[label]:!o[label]}))}}><I size={17}/><span className="hide-collapse nav-label">{label}</span>{children2&&<Icons.ChevronDown size={14} className={`hide-collapse chev ${open[label]?'rot':''}`}/>}</button>{children2&&open[label]&&<div className="hide-collapse">{children2.map((c:any)=><button className={`nav-item nav-child ${loc.pathname===c[1]?'active':''}`} key={c[1]} onClick={()=>{if(c[1]==='/logout'){logoutNtaxco()}else{navg(c[1]);setMobile(false)}}}>{c[0]}</button>)}</div>}</div>})}</div></aside><main className={`main ${collapsed?'expanded':''}`}><header className="topbar"><button className="btn icon-btn" onClick={()=>window.innerWidth<760?setMobile(!mobile):setCollapsed(!collapsed)}><Icons.Menu size={18}/></button><div className="global-search"><Icons.Search size={16}/><input className="input" placeholder="Search anything..." value={q} onChange={e=>setQ(e.target.value)}/>{results.length>0&&<div className="search-results card">{results.map(r=><button key={r[1]+r[0]} className="search-result" onClick={()=>{navg(r[2]);setQ('')}}><span>{r[0]}</span><small>{r[1]}</small></button>)}</div>}</div><button className="btn create-btn" onClick={()=>setCreate(true)}><Icons.Plus size={17}/>Create</button><button className="btn icon-btn" onClick={()=>navg('/notifications')}><Icons.Bell size={17}/><i className="notification-dot"/></button><button className="profile-chip" onClick={()=>navg('/profile')}><span className="avatar">{user.name.slice(0,1).toUpperCase()}</span><span>{user.name}</span></button></header><div className="content">{children}</div></main>{create&&<CreateMenu close={()=>setCreate(false)}/>}<nav className="mobile-nav"><button onClick={()=>navg('/')}><Icons.LayoutDashboard/>Home</button><button onClick={()=>navg('/sales/pos')}><Icons.Receipt/>POS</button><button onClick={()=>navg('/inventory/products')}><Icons.Package/>Stock</button><button onClick={()=>navg('/utilities/calculator')}><Icons.Calculator/>Calc</button><button onClick={()=>setMobile(true)}><Icons.Menu/>More</button></nav></div>}

function CreateMenu({close}:{close:()=>void}){const n=useNavigate();const items=[['New Invoice','/sales/invoices',Icons.FilePlus],['New POS Bill','/sales/pos',Icons.Receipt],['New Customer','/parties/customers',Icons.UserPlus],['New Supplier','/parties/suppliers',Icons.Users],['New Product','/inventory/products',Icons.PackagePlus],['New Purchase','/purchases/bills',Icons.ShoppingCart],['New Payment','/payments/receipts',Icons.Wallet],['New Expense','/expenses',Icons.BadgeIndianRupee],['New Quotation','/sales/quotations',Icons.FileText],['New Calendar Event','/utilities/calendar',Icons.Calendar]];return <div className="overlay" onClick={close}><div className="create-menu card" onClick={e=>e.stopPropagation()}><div className="modal-head"><div><h2>Quick Create</h2><p className="muted small">Open an existing BillBooks workflow.</p></div><button className="btn icon-btn" onClick={close}><Icons.X/></button></div>{items.map(([label,path,I]:any)=><button key={label} className="create-item" onClick={()=>{n(path);close()}}><span className="icon-tile">{<I size={18}/>}</span>{label}<Icons.ChevronRight size={16}/></button>)}</div></div>}

function PageHead({title,sub,actions}:{title:string;sub?:string;actions?:React.ReactNode}){return <div className="page-head"><div><div className="eyebrow">BILLBOOKS</div><h1>{title}</h1>{sub&&<p className="muted">{sub}</p>}</div><div className="actions">{actions}</div></div>}
function Card({children,className='' }:{children:React.ReactNode;className?:string}){return <div className={`card ${className}`}>{children}</div>}
function Table({headers,rows}:{headers:string[];rows:React.ReactNode[][]}){return <div className="table-wrap"><table className="table"><thead><tr>{headers.map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{rows.length?rows.map((r,i)=><tr key={i}>{r.map((v,j)=><td key={j}>{v}</td>)}</tr>):<tr><td colSpan={headers.length}><div className="empty"><Icons.Inbox size={28}/><b>No records</b><span>No matching data found for this business.</span></div></td></tr>}</tbody></table></div>}
function Badge({text}:{text:string}){const good=['Paid','Completed','Active','In Stock','Confirmed','Processing'].includes(text);const warn=['Pending','Expiring Soon','Draft','Low Stock'].includes(text);const bad=['Overdue','Expired','Cancelled','Out of Stock'].includes(text);return <span className={`badge ${good?'green':warn?'amber':bad?'red':'blue'}`}>{text}</span>}
function ActionButtons({onAdd,onExport}:{onAdd?:()=>void;onExport?:()=>void}){return <div className="actions">{onExport&&<button className="btn" onClick={onExport}><Icons.Download size={15}/>Export</button>}{onAdd&&<button className="btn primary" onClick={onAdd}><Icons.Plus size={15}/>Add New</button>}</div>}
function exportCsv(filename:string,headers:string[],rows:(string|number)[][]){const esc=(v:any)=>`"${String(v??'').replaceAll('"','""')}"`;const blob=new Blob([[headers,...rows].map(r=>r.map(esc).join(',')).join('\n')],{type:'text/csv;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=filename;a.click();URL.revokeObjectURL(a.href)}
function Modal({title,children,onClose,onSave,saveText='Save'}:{title:string;children:React.ReactNode;onClose:()=>void;onSave?:()=>void;saveText?:string}){return <div className="overlay"><div className="modal card"><div className="modal-head"><h2>{title}</h2><button className="btn icon-btn" onClick={onClose}><Icons.X/></button></div><div>{children}</div>{onSave&&<div className="modal-actions"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={onSave}>{saveText}</button></div>}</div></div>}

function Dashboard({data}:{data:UserData}){const t=totals(data),n=useNavigate();const monthKeys=Array.from({length:6},(_,i)=>{const d=new Date();d.setDate(1);d.setMonth(d.getMonth()-(5-i));return {key:`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`,m:d.toLocaleDateString('en-IN',{month:'short'})}});const chart=monthKeys.map(m=>({m:m.m,sales:data.invoices.filter((x:any)=>String(x.date||'').slice(0,7)===m.key).reduce((a:number,x:any)=>a+Number(x.total||0),0),purchases:data.purchases.filter((x:any)=>String(x.date||'').slice(0,7)===m.key).reduce((a:number,x:any)=>a+Number(x.total||0),0)}));const cards=[['Total Sales',t.sales,'#3b82f6',Icons.Receipt],['Total Purchases',t.purchases,'#f59e0b',Icons.ShoppingCart],['Receivables',t.receivables,'#22c55e',Icons.ArrowDownToLine],['Expenses',t.expenses,'#f97316',Icons.BadgeIndianRupee],['Net Profit',t.profit,'#8b5cf6',Icons.ChartNoAxesCombined],['Low Stock',t.lowStock,'#eab308',Icons.AlertTriangle],['Bank Balance',t.bank,'#06b6d4',Icons.Landmark],['Cash Balance',t.cash,'#14b8a6',Icons.WalletCards]] as const;return <><PageHead title={`Good Morning, ${data.businessProfile.ownerName}`} sub="A colorful view of your current BillBooks business." actions={<button className="btn primary" onClick={()=>n('/sales/pos')}><Icons.Plus/>New POS Bill</button>}/><div className="grid grid-4">{cards.map(([label,val,color,I])=><div className="card kpi" key={label} style={{'--accent':color} as React.CSSProperties}><span className="kpi-icon"><I size={18}/></span><span className="muted small">{label}</span><strong>{label==='Low Stock'?val:money(val)}</strong></div>)}</div><div className="quick-tools"><b>Quick Actions</b>{[['POS Billing','/sales/pos',Icons.Receipt],['New Customer','/parties/customers',Icons.UserPlus],['Products','/inventory/products',Icons.Package],['Payment Reminder','/payments/reminders',Icons.Bell],['Calendar','/utilities/calendar',Icons.Calendar]].map(([x,to,I]:any)=><button className="btn" key={x} onClick={()=>n(to)}><I size={16}/>{x}</button>)}</div><div className="grid grid-2"><Card className="panel"><div className="section-head"><h3>Sales & Purchases</h3><Badge text="Saved Records"/></div><div className="chart"><ResponsiveContainer><AreaChart data={chart}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="m"/><YAxis/><Tooltip/><Legend/><Area dataKey="sales" fill="#dbeafe" stroke="#3b82f6" name="Sales"/><Area dataKey="purchases" fill="#ffedd5" stroke="#f59e0b" name="Purchases"/></AreaChart></ResponsiveContainer></div></Card><Card className="panel"><h3>Business Alerts</h3><Alert label="Low Stock" value={String(t.lowStock)} to="/inventory/low-stock"/><Alert label="Expiring Batches" value={String(t.expiring)} to="/inventory/batches"/><Alert label="Receivables" value={money(t.receivables)} to="/payments/reminders"/><Alert label="Pending Reminders" value={String(data.reminders.length)} to="/payments/reminders"/></Card></div><Card className="panel"><div className="section-head"><h3>Recent Invoices</h3><button className="btn" onClick={()=>n('/sales/invoices')}>View all</button></div><Table headers={['Invoice','Customer','Amount','Payment','Status','Date']} rows={data.invoices.slice(0,7).map(i=>[i.id,i.customer,money(i.total),i.payment,<Badge text={i.status}/>,i.date])}/></Card></>}
function Alert({label,value,to}:{label:string;value:string;to:string}){const n=useNavigate();return <button className="alert-row" onClick={()=>n(to)}><span>{label}</span><b>{value}</b><Icons.ChevronRight size={16}/></button>}

function POS({data,save}: {data:UserData;save:Save}){const [q,setQ]=useState(''),[cart,setCart]=useState<InvoiceItem[]>([]),[customer,setCustomer]=useState(''),[payment,setPayment]=useState('Cash');const matches=data.products.filter(p=>(p.name+' '+p.barcode+' '+p.sku).toLowerCase().includes(q.toLowerCase())).filter(p=>p.stock>0);const add=(p:Product)=>{const old=cart.find(i=>i.productId===p.id);setCart(old?cart.map(i=>i.productId===p.id?{...i,qty:Math.min(p.stock,i.qty+1)}:i):[...cart,{productId:p.id,name:p.name,qty:1,price:p.price,discount:0,tax:p.tax}]);setQ('')};const total=cart.reduce((a,i)=>a+i.qty*i.price*(1+i.tax/100)-i.discount,0);const complete=()=>{if(!cart.length)return;const c=data.customers.find(x=>x.id===customer);const id=`INV-${String(data.invoices.length+1).padStart(3,'0')}`;const inv={id,customerId:c?.id||'',customer:c?.name||'Walk-in Customer',date:today(),dueDate:today(),items:clone(cart),status:'Paid',payment,total:Math.round(total)};const pay={id:uid('pay'),party:inv.customer,type:'Receipt',amount:inv.total,mode:payment,reference:id,date:today(),status:'Completed',invoice:id};const products=data.products.map(p=>{const it=cart.find(i=>i.productId===p.id);return it?{...p,stock:Math.max(0,p.stock-it.qty)}:p});const customers=data.customers.map(x=>x.id===c?.id?{...x,sales:x.sales+inv.total,received:x.received+inv.total,status:'Paid'}:x);save({...data,invoices:[inv,...data.invoices],payments:[pay,...data.payments],products,customers,posBills:[{id:uid('pos'),customerId:c?.id,items:cart,total:inv.total,paymentMode:payment,createdAt:new Date().toISOString()},...data.posBills],auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:'Created POS sale',module:'Sales',reference:id},...data.auditLogs]});setCart([]);alert(`Bill ${id} completed`) };return <><PageHead title="POS Billing" sub=" • stock, invoice, payment, ledger and audit data update together."/><div className="demo-note"></div><div className="pos-layout"><Card className="panel"><div className="pos-search"><Icons.ScanBarcode/><input className="input" placeholder="Search / Scan Barcode" value={q} onChange={e=>setQ(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&matches[0])add(matches[0])}}/>{q&&<div className="suggestions">{matches.slice(0,7).map(p=><button key={p.id} onClick={()=>add(p)}>{p.name}<small>{p.barcode} • {money(p.price)}</small></button>)}</div>}</div><div className="product-grid">{data.products.map(p=><button className="product-tile" key={p.id} onClick={()=>p.stock>0&&add(p)}><span className="product-color"/><b>{p.name}</b><strong>{money(p.price)}</strong><small>{p.stock} in stock</small></button>)}</div></Card><Card className="panel cart-panel"><div className="section-head"><h3>Cart</h3><button className="btn" onClick={()=>setCart([])}>Clear</button></div><label className="label">Customer</label><select className="select" value={customer} onChange={e=>setCustomer(e.target.value)}><option value="">Walk-in Customer</option>{data.customers.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select><div className="cart-items">{cart.map(i=><div className="cart-item" key={i.productId}><div><b>{i.name}</b><small>{money(i.price)} • GST {i.tax}%</small></div><div className="qty"><button onClick={()=>setCart(cart.map(x=>x.productId===i.productId?{...x,qty:Math.max(1,x.qty-1)}:x))}>−</button><b>{i.qty}</b><button onClick={()=>setCart(cart.map(x=>x.productId===i.productId?{...x,qty:x.qty+1}:x))}>+</button></div><strong>{money(i.qty*i.price*(1+i.tax/100)-i.discount)}</strong><button className="icon-btn" onClick={()=>setCart(cart.filter(x=>x.productId!==i.productId))}><Icons.X size={15}/></button></div>)}</div><div className="totals"><span>Subtotal</span><b>{money(cart.reduce((a,i)=>a+i.qty*i.price,0))}</b><span>Tax</span><b>{money(cart.reduce((a,i)=>a+i.qty*i.price*i.tax/100,0))}</b><strong>Grand Total</strong><strong>{money(total)}</strong></div><div className="pay-buttons">{['Cash','Card','UPI','Split Payment'].map(x=><button className={`btn ${payment===x?'primary':''}`} key={x} onClick={()=>setPayment(x)}>{x}</button>)}</div><div className="pos-actions"><button className="btn" onClick={()=>{if(cart.length){save({...data,heldBills:[...data.heldBills,{id:uid('HB'),customerId:customer,customerName:data.customers.find(c=>c.id===customer)?.name||'Walk-in Customer',items:cart,total,heldAt:new Date().toLocaleTimeString()}]});setCart([])}}}>Hold Bill</button><button className="btn primary" onClick={complete}>Save / Complete Bill</button><button className="btn" onClick={()=>window.print()}><Icons.Printer/>Print Bill</button></div></Card></div></>}

function Invoices({data,save}: {data:UserData;save:Save}){
 const [show,setShow]=useState(false),[selected,setSelected]=useState<any>(null),[editing,setEditing]=useState<any>(null),[q,setQ]=useState(''),[filter,setFilter]=useState('All'),[customer,setCustomer]=useState(data.customers[0]?.id||''),[date,setDate]=useState(new Date().toISOString().slice(0,10)),[dueDate,setDueDate]=useState(new Date().toISOString().slice(0,10)),[items,setItems]=useState<InvoiceItem[]>([{productId:'',name:'',qty:1,price:0,discount:0,tax:18}]),[notes,setNotes]=useState(''),[terms,setTerms]=useState('');
 const moneyNum=(v:number)=>Math.round((v+Number.EPSILON)*100)/100;
 const itemBase=(it:InvoiceItem)=>moneyNum(Math.max(0,(Number(it.qty)||0)*(Number(it.price)||0)-(Number(it.discount)||0)));
 const subtotal=moneyNum(items.reduce((a,it)=>a+itemBase(it),0)),taxAmount=moneyNum(items.reduce((a,it)=>a+itemBase(it)*(Number(it.tax)||0)/100,0)),amount=moneyNum(subtotal+taxAmount),roundOff=moneyNum(Math.round(amount)-amount),total=moneyNum(amount+roundOff);
 const paymentsFor=(i:any)=>data.payments.filter(p=>p.type==='Invoice Payment'&&(p.invoice===i.id||p.reference===i.id)&&p.status!=='Voided'&&Number(p.amount)>0);
 const paidAmount=(i:any)=>{const ps=paymentsFor(i);return moneyNum(ps.length?ps.reduce((a,p)=>a+Number(p.amount||0),0):Number(i.paidAmount??(i.payment==='Paid'?i.total:0)))};
 const statusOf=(i:any)=>{const p=paidAmount(i),bal=Math.max(0,Number(i.total)-p);if(bal<=0&&Number(i.total)>0)return 'Paid';if(p>0)return 'Partially Paid';if(i.status==='Draft')return 'Draft';if(i.dueDate&&i.dueDate<new Date().toISOString().slice(0,10))return 'Overdue';return i.status==='Sent'?'Sent':'Unpaid'};
 const toDate=(v:string)=>{if(!v)return new Date().toISOString().slice(0,10);const d=new Date(v);return Number.isNaN(d.getTime())?new Date().toISOString().slice(0,10):d.toISOString().slice(0,10)};
 const reset=()=>{setEditing(null);setCustomer(data.customers[0]?.id||'');setDate(new Date().toISOString().slice(0,10));setDueDate(new Date().toISOString().slice(0,10));setItems([{productId:'',name:'',qty:1,price:0,discount:0,tax:18}]);setNotes(data.invoiceSettings.notes||'');setTerms(data.invoiceSettings.terms||'');setShow(true)};
 const edit=(i:any)=>{setEditing(i);setCustomer(i.customerId||data.customers[0]?.id||'');setDate(toDate(i.date));setDueDate(toDate(i.dueDate));setItems(i.items?.length?i.items.map((x:InvoiceItem)=>({...x})): [{productId:'',name:'Historical item',qty:1,price:Number(i.total)||0,discount:0,tax:0}]);setNotes(i.notes||'');setTerms(i.terms||'');setShow(true)};
 const create=()=>{const c=data.customers.find(x=>x.id===customer);if(!c){alert('Please add/select a customer first.');return}if(!items.length||items.some(it=>!it.name.trim()||!Number.isFinite(Number(it.qty))||Number(it.qty)<=0||!Number.isFinite(Number(it.price))||Number(it.price)<0||Number(it.discount)<0||Number(it.discount)>Number(it.qty)*Number(it.price)||Number(it.tax)<0)){alert('Check each item description, quantity, rate, discount and tax rate.');return}
 const id=editing?.id||`${data.invoiceSettings.prefix||'INV-'}${String(data.invoices.length+1).padStart(4,'0')}`;if(!editing&&data.invoices.some(i=>i.id===id)){alert(`Invoice number ${id} already exists. Change the prefix in Invoice Customization or remove the conflicting draft.`);return}
 const old=editing?data.invoices.find(i=>i.id===editing.id):undefined;const paid=old?paidAmount(old):0;if(old&&total<paid){alert(`The edited total (${money(total)}) cannot be below recorded payments (${money(paid)}). Record a credit/refund or correct the payment records first.`);return}
 if(old&&['Paid','Sent','Overdue','Partially Paid'].includes(statusOf(old))&&!confirm(`This invoice is ${statusOf(old)}. Save a correction while preserving invoice number, creation time, and payment history?`))return;
 const inv:any={...(editing||{}),id,customerId:c.id,customer:c.name,date,dueDate,items:items.map(it=>({...it,qty:Number(it.qty),price:Number(it.price),discount:Number(it.discount)||0,tax:Number(it.tax)||0})),subtotal,taxAmount,roundOff,total,notes,terms,status:old?.status||(statusOf({status:'Unpaid',dueDate,total,paidAmount:0})==='Overdue'?'Overdue':'Sent'),payment:paid>=total&&total>0?'Paid':paid>0?'Partially Paid':'Unpaid',paidAmount:paid,updatedAt:new Date().toISOString(),createdAt:editing?.createdAt||new Date().toISOString()};
 const nextInvoices=editing?data.invoices.map(i=>i.id===id?inv:i):[inv,...data.invoices];const customerSalesDelta=total-(old?.total||0);save({...data,invoices:nextInvoices,customers:data.customers.map(x=>x.id===c.id?{...x,sales:Math.max(0,x.sales+customerSalesDelta),status:'Receivable'}:x),auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:editing?'Corrected invoice':'Created invoice',module:'Sales',reference:id},...data.auditLogs]});setShow(false);setEditing(null)};
 const list=data.invoices.filter(i=>(`${i.id} ${i.customer} ${data.customers.find(c=>c.id===i.customerId)?.gstin||''}`).toLowerCase().includes(q.toLowerCase())&&(filter==='All'||statusOf(i)===filter));const totals2={all:data.invoices.length,draft:data.invoices.filter(i=>statusOf(i)==='Draft').length,sent:data.invoices.filter(i=>statusOf(i)==='Sent').length,paid:data.invoices.filter(i=>statusOf(i)==='Paid').length,unpaid:data.invoices.filter(i=>['Unpaid','Partially Paid'].includes(statusOf(i))).length,overdue:data.invoices.filter(i=>statusOf(i)==='Overdue').length,amount:data.invoices.reduce((a,i)=>a+i.total,0),balance:data.invoices.reduce((a,i)=>a+Math.max(0,i.total-paidAmount(i)),0)};
 const recordPayment=(i:any)=>{const balance=Math.max(0,i.total-paidAmount(i));const raw=prompt(`Balance due: ${money(balance)}\nEnter payment received:`,String(balance));if(raw===null)return;const val=Number(raw);if(!Number.isFinite(val)||val<=0||val>balance){alert('Enter a payment greater than zero and no more than the balance due.');return}const paymentId=uid('payment');const existingRecords=paymentsFor(i);const legacyPayment=existingRecords.length?[]:(Number(i.paidAmount??(i.payment==='Paid'?i.total:0))>0?[{id:uid('payment'),party:i.customer,type:'Invoice Payment',amount:moneyNum(Number(i.paidAmount??(i.payment==='Paid'?i.total:0))),mode:'Legacy recorded payment',reference:i.id,date:i.date||today(),status:'Recorded',invoice:i.id} as Payment]:[]);save({...data,payments:[{id:paymentId,party:i.customer,type:'Invoice Payment',amount:moneyNum(val),mode:'Bank/UPI',reference:i.id,date:today(),status:'Recorded',invoice:i.id},...legacyPayment,...data.payments],invoices:data.invoices.map(x=>x.id===i.id?{...x,paidAmount:moneyNum(paidAmount(i)+val),payment:paidAmount(i)+val>=i.total?'Paid':'Partially Paid'}:x),customers:data.customers.map(c=>c.id===i.customerId?{...c,received:c.received+val}:c),auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:`Recorded payment ${money(val)}`,module:'Invoices',reference:i.id},...data.auditLogs]})};
 return <><PageHead title="Invoices" sub="Manage invoices, balances, payment records and printable invoice documents." actions={<button className="btn primary" onClick={reset}><Icons.Plus/> Create Invoice</button>}/>
 <div className="grid grid-4">{[['Total Invoices',totals2.all],['Draft',totals2.draft],['Sent',totals2.sent],['Paid',totals2.paid],['Unpaid / Partial',totals2.unpaid],['Overdue',totals2.overdue],['Invoice Amount',money(totals2.amount)],['Outstanding',money(totals2.balance)]].map(([l,v]:any)=><Card key={l} className="stat-card"><span className="muted small">{l}</span><h2>{v}</h2></Card>)}</div>
 <Card className="search-card"><div className="grid grid-2"><input className="input" placeholder="Search invoice number, customer or GSTIN..." value={q} onChange={e=>setQ(e.target.value)}/><select className="select" value={filter} onChange={e=>setFilter(e.target.value)}>{['All','Draft','Sent','Unpaid','Partially Paid','Paid','Overdue'].map(x=><option key={x}>{x}</option>)}</select></div></Card>
 <Card>{list.length?<Table headers={['Invoice Number','Invoice Date','Due Date','Customer','Subtotal','Tax','Total','Paid','Balance','Status','Actions']} rows={list.map(i=>{const paidV=paidAmount(i);return [i.id,i.date,i.dueDate,i.customer,money((i as any).subtotal??i.total),money((i as any).taxAmount??0),money(i.total),money(paidV),money(Math.max(0,i.total-paidV)),<Badge text={statusOf(i)}/>,<div className="actions"><button className="btn" onClick={()=>setSelected(i)}>View</button><button className="btn" onClick={()=>edit(i)}>Edit</button><button className="btn" onClick={()=>recordPayment(i)} disabled={paidV>=i.total}>Payment</button><button className="btn" onClick={()=>{const idBase=`${data.invoiceSettings.prefix||'INV-'}${String(data.invoices.length+1).padStart(4,'0')}`;if(data.invoices.some(x=>x.id===idBase)){alert('Invoice number already exists. Update the invoice prefix before duplicating.');return}const copy={...i,id:idBase,status:'Draft',payment:'Unpaid',paidAmount:0,createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()};save({...data,invoices:[copy,...data.invoices],auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:'Duplicated invoice as draft',module:'Invoices',reference:idBase},...data.auditLogs]})}}>Duplicate</button>{statusOf(i)==='Draft'&&<button className="btn danger" onClick={()=>{if(!confirm(`Delete draft ${i.id}?`))return;save({...data,invoices:data.invoices.filter(x=>x.id!==i.id),auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:'Deleted draft invoice',module:'Invoices',reference:i.id},...data.auditLogs]})}}>Delete Draft</button>}</div>]})}/>:<div className="empty-state"><Icons.FileText size={32}/><h3>No invoices found</h3><p className="muted">Create an invoice or adjust your search and filters.</p><button className="btn primary" onClick={reset}>Create Invoice</button></div>}</Card>
 {show&&<Modal title={editing?`Edit ${editing.id}`:'Create Invoice'} onClose={()=>{setShow(false);setEditing(null)}} onSave={create}><div className="grid grid-2"><label className="field"><span>Customer *</span><select className="select" value={customer} onChange={e=>setCustomer(e.target.value)}>{data.customers.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label><label className="field"><span>Invoice Date</span><input className="input" type="date" value={date} onChange={e=>setDate(e.target.value)}/></label><label className="field"><span>Due Date</span><input className="input" type="date" min={date} value={dueDate} onChange={e=>setDueDate(e.target.value)}/></label></div>
 <h3>Line Items</h3>{items.map((it,idx)=><div className="card" key={idx}><div className="grid grid-2"><label className="field"><span>Description *</span><input className="input" value={it.name} onChange={e=>setItems(xs=>xs.map((x,j)=>j===idx?{...x,name:e.target.value}:x))}/></label><label className="field"><span>Quantity</span><input className="input" type="number" min="0.01" step="0.01" value={it.qty} onChange={e=>setItems(xs=>xs.map((x,j)=>j===idx?{...x,qty:Number(e.target.value)}:x))}/></label><label className="field"><span>Rate (₹)</span><input className="input" type="number" min="0" step="0.01" value={it.price} onChange={e=>setItems(xs=>xs.map((x,j)=>j===idx?{...x,price:Number(e.target.value)}:x))}/></label><label className="field"><span>Discount (₹)</span><input className="input" type="number" min="0" step="0.01" value={it.discount} onChange={e=>setItems(xs=>xs.map((x,j)=>j===idx?{...x,discount:Number(e.target.value)}:x))}/></label><label className="field"><span>GST Rate (%)</span><select className="select" value={it.tax} onChange={e=>setItems(xs=>xs.map((x,j)=>j===idx?{...x,tax:Number(e.target.value)}:x))}>{['0','5','12','18','28'].map(x=><option key={x} value={x}>{x}%</option>)}</select></label><label className="field"><span>Line Total</span><input className="input" readOnly value={money(itemBase(it)+itemBase(it)*(Number(it.tax)||0)/100)}/></label></div>{items.length>1&&<button className="btn danger" type="button" onClick={()=>setItems(xs=>xs.filter((_,j)=>j!==idx))}>Remove Item</button>}</div>)}<button className="btn" type="button" onClick={()=>setItems(xs=>[...xs,{productId:'',name:'',qty:1,price:0,discount:0,tax:18}])}><Icons.Plus size={15}/> Add Item</button>
 <div className="grid grid-2"><label className="field"><span>Invoice Notes</span><textarea className="input" value={notes} onChange={e=>setNotes(e.target.value)}/></label><label className="field"><span>Terms & Conditions</span><textarea className="input" value={terms} onChange={e=>setTerms(e.target.value)}/></label></div><div className="invoice-summary"><div><span>Taxable subtotal</span><b>{money(subtotal)}</b><span>GST</span><b>{money(taxAmount)}</b><span>Round-off</span><b>{money(roundOff)}</b><strong>Invoice total</strong><strong>{money(total)}</strong><span>Recorded payments</span><b>{money(editing?paidAmount(editing):0)}</b><span>Outstanding</span><b>{money(Math.max(0,total-(editing?paidAmount(editing):0)))}</b></div></div><p className="muted small">Payment history is preserved as individual records. To record a new payment, use the Payment action after saving. Company branding is managed in Company Profile and Invoice Customization.</p></Modal>}
 {selected&&<Modal title={`Invoice ${selected.id}`} onClose={()=>setSelected(null)}><div className="invoice-document"><header className="invoice-company">{(data.invoiceSettings.logo||data.businessProfile.logo)&&<img src={data.invoiceSettings.logo||data.businessProfile.logo} alt="Company logo" style={{maxWidth:110,maxHeight:70,objectFit:'contain'}}/>}<div><h2>{data.invoiceSettings.businessName||data.businessProfile.businessName||'Business Name'}</h2><p>{data.invoiceSettings.address||data.businessProfile.address}</p><p>{data.invoiceSettings.phone||data.businessProfile.mobile} · {data.invoiceSettings.email||data.businessProfile.email}</p><p>GSTIN: {data.invoiceSettings.gstin||data.businessProfile.gstin||'—'}</p><p>PAN: {(data.businessProfile as any).pan||'—'}</p><p>{data.invoiceSettings.bankDetails||''}</p></div><div className="invoice-title"><h2>TAX INVOICE</h2><b>{selected.id}</b><p>{selected.date}</p><p>Due: {selected.dueDate}</p><Badge text={statusOf(selected)}/></div></header><div className="invoice-customer"><b>Bill To</b><p>{selected.customer}</p><p>{data.customers.find(c=>c.id===selected.customerId)?.address||''}</p><p>GSTIN: {data.customers.find(c=>c.id===selected.customerId)?.gstin||'—'}</p></div><div className="table-wrap"><table className="table invoice-items"><thead><tr><th>S.No.</th><th>Product / Service</th><th>Qty</th><th>Rate (₹)</th><th>Discount (₹)</th><th>GST (%)</th><th>Tax (₹)</th><th>Total (₹)</th></tr></thead><tbody>{(selected.items||[]).map((it:any,idx:number)=>{const base=Math.max(0,it.qty*it.price-(it.discount||0)),taxAmt=base*(it.tax||0)/100;return <tr key={idx}><td>{idx+1}</td><td>{it.name}</td><td>{it.qty}</td><td>{money(it.price)}</td><td>{money(it.discount||0)}</td><td>{it.tax||0}%</td><td>{money(taxAmt)}</td><td>{money(base+taxAmt)}</td></tr>})}</tbody></table></div><div className="invoice-summary"><div><span>Subtotal / taxable amount</span><b>{money((selected as any).subtotal??selected.total)}</b><span>GST</span><b>{money((selected as any).taxAmount??0)}</b><span>Round-off</span><b>{money((selected as any).roundOff??0)}</b><span>Amount paid</span><b>{money(paidAmount(selected))}</b><strong>Balance due</strong><strong>{money(Math.max(0,selected.total-paidAmount(selected)))}</strong><strong>Grand Total</strong><strong>{money(selected.total)}</strong></div></div><p className="invoice-footer">{selected.notes||data.invoiceSettings.notes||'Thank you for your business.'}</p><p className="invoice-footer">{selected.terms||data.invoiceSettings.terms}</p></div><div className="actions end"><button className="btn primary" onClick={()=>window.print()}><Icons.Printer size={16}/> Print / Save PDF</button><button className="btn" onClick={()=>{const blob=new Blob([JSON.stringify(selected,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${selected.id}.json`;a.click();URL.revokeObjectURL(a.href)}}><Icons.Download size={16}/> Export JSON</button></div></Modal>}
 </>;
}
function Customers({data,save}: {data:UserData;save:Save}){
 const [show,setShow]=useState(false),[editing,setEditing]=useState<any>(null),[name,setName]=useState(''),[phone,setPhone]=useState(''),[gstin,setGstin]=useState(''),[filter,setFilter]=useState('All'),[q,setQ]=useState(''),[view,setView]=useState('Table');
 const customerFinancials=(c:any)=>{const invoices=data.invoices.filter((i:any)=>i.customerId===c.id||(i.customerId==null&&i.customer===c.name));const sales=invoices.length?invoices.reduce((a:any,i:any)=>a+Number(i.total||0),0):Number(c.sales||0);const ids=new Set(invoices.map((i:any)=>i.id));const receipts=data.payments.filter((p:any)=>p.status!=='Voided'&&Number(p.amount)>0&&(p.customerId===c.id||p.partyId===c.id||p.party===c.name||ids.has(p.invoice)||ids.has(p.reference)));const received=receipts.length?receipts.reduce((a:any,p:any)=>a+Number(p.amount||0),0):Number(c.received||0);return {sales,received,outstanding:Math.max(0,sales-received),status:sales>0&&received>=sales?'Paid':received>0?'Partially Paid':'Receivable'}};
 const list=data.customers.map((c:any)=>({...c,...customerFinancials(c)})).filter((c:any)=>{const f=filter==='GST'?!!c.gstin:filter==='Paid'?c.status==='Paid':filter==='Pending'?c.outstanding>0:filter==='Completed'?c.status==='Paid'||c.status==='Completed':true;return f&&`${c.name} ${c.phone||''} ${c.gstin||''}`.toLowerCase().includes(q.toLowerCase())});
 const openNew=()=>{setEditing(null);setName('');setPhone('');setGstin('');setShow(true)};const openEdit=(c:any)=>{setEditing(c);setName(c.name||'');setPhone(c.phone||'');setGstin(c.gstin||'');setShow(true)};
 const persist=()=>{if(!name.trim()){alert('Customer name is required.');return}const next=editing?data.customers.map((c:any)=>c.id===editing.id?{...c,name:name.trim(),phone:phone.trim(),gstin:gstin.trim()}:c):[{id:uid('customer'),name:name.trim(),phone:phone.trim(),gstin:gstin.trim(),sales:0,received:0,status:'Receivable'},...data.customers];save({...data,customers:next});setShow(false)};
 const rows=list.map((c:any)=>[c.name,c.phone||'—',c.gstin||'—',money(c.sales),money(c.received),money(c.outstanding),<Badge text={c.status}/>,<button className="btn" onClick={()=>openEdit(c)}>Edit</button>]);
 const totalSales=list.reduce((a:any,c:any)=>a+c.sales,0),totalReceived=list.reduce((a:any,c:any)=>a+c.received,0),totalOutstanding=list.reduce((a:any,c:any)=>a+c.outstanding,0);
 return <><PageHead title="Customers" sub="Customer Ledger is shared with Accounting and Parties; no duplicate store is created." actions={<><button className="btn" onClick={()=>exportCsv('billbook-customers.csv',['Customer','Mobile','GSTIN','Sales','Received','Outstanding','Status'],list.map((c:any)=>[c.name,c.phone||'',c.gstin||'',c.sales,c.received,c.outstanding,c.status]))}><Icons.Download size={15}/>Export</button><button className="btn primary" onClick={openNew}><Icons.UserPlus/>New Customer</button></>}/>
 <div className="grid grid-4"><Stat label="Total Customers" value={data.customers.length}/><Stat label="Paid Customers" value={data.customers.filter((c:any)=>customerFinancials(c).status==='Paid').length}/><Stat label="Pending Customers" value={data.customers.filter((c:any)=>customerFinancials(c).outstanding>0).length}/><Stat label="Outstanding" value={money(totalOutstanding)}/></div>
 <div className="filter-pills">{['All','GST','Paid','Pending','Completed'].map(x=><button key={x} className={filter===x?'filter-active':''} onClick={()=>setFilter(x)}>{x}</button>)}</div><Card className="search-card"><div className="row-between"><input className="input" placeholder="Search customers, mobile or GSTIN..." value={q} onChange={e=>setQ(e.target.value)}/><div className="actions">{['Cards','Table','Kanban'].map(x=><button key={x} className={`btn ${view===x?'primary':''}`} onClick={()=>setView(x)}>{x==='Cards'?<Icons.LayoutGrid size={15}/>:x==='Table'?<Icons.List size={15}/>:<Icons.Columns3 size={15}/>} {x}</button>)}</div></div><p className="muted small">Showing {list.length} of {data.customers.length} customer(s) · Sales {money(totalSales)} · Received {money(totalReceived)} · Outstanding {money(totalOutstanding)}</p></Card>
 {view==='Table'?<Card><Table headers={['Customer','Mobile','GSTIN','Sales','Received','Outstanding','Status','Actions']} rows={rows}/></Card>:view==='Cards'?<div className="grid grid-3">{list.map((c:any)=><Card className="panel" key={c.id}><div className="row-between"><span className="avatar">{String(c.name||'?').slice(0,1).toUpperCase()}</span><Badge text={c.status}/></div><h3>{c.name}</h3><p className="muted small">{c.phone||'No mobile'} · {c.gstin||'No GSTIN'}</p><div className="grid grid-3"><div><small>Sales</small><b>{money(c.sales)}</b></div><div><small>Received</small><b>{money(c.received)}</b></div><div><small>Due</small><b>{money(c.outstanding)}</b></div></div><button className="btn" onClick={()=>openEdit(c)}>Edit Customer</button></Card>)}</div>:<div className="grid grid-3">{['Receivable','Partially Paid','Paid'].map(status=><Card className="panel" key={status}><h3>{status}</h3>{list.filter((c:any)=>c.status===status).map((c:any)=><div className="list-row" key={c.id}><div><b>{c.name}</b><p className="muted small">{c.phone||'—'} · {money(c.outstanding)} outstanding</p></div><button className="btn" onClick={()=>openEdit(c)}>Edit</button></div>)}</Card>)}</div>}
 {show&&<Modal title={editing?'Edit Customer':'New Customer'} onClose={()=>setShow(false)} onSave={persist}><label className="label">Customer Name *</label><input className="input" value={name} onChange={e=>setName(e.target.value)}/><label className="label">Mobile</label><input className="input" value={phone} onChange={e=>setPhone(e.target.value)}/><label className="label">GSTIN</label><input className="input" value={gstin} onChange={e=>setGstin(e.target.value.toUpperCase())}/></Modal>}</>
}

function SalesOrders({data,save}:{data:UserData;save:Save}){
 const [show,setShow]=useState(false),[editing,setEditing]=useState<any>(null),[q,setQ]=useState(''),[filter,setFilter]=useState('All'),[customerId,setCustomerId]=useState(data.customers[0]?.id||''),[date,setDate]=useState(new Date().toISOString().slice(0,10)),[status,setStatus]=useState('Pending'),[productId,setProductId]=useState(data.products[0]?.id||''),[qty,setQty]=useState('1'),[items,setItems]=useState<any[]>([]);
 const reset=()=>{setEditing(null);setCustomerId(data.customers[0]?.id||'');setDate(new Date().toISOString().slice(0,10));setStatus('Pending');setItems([]);setProductId(data.products[0]?.id||'');setQty('1');setShow(true)};
 const edit=(o:any)=>{setEditing(o);setCustomerId(o.customerId||data.customers.find((c:any)=>c.name===o.customer)?.id||'');setDate((o.date||'').slice(0,10)||new Date().toISOString().slice(0,10));setStatus(o.status||'Pending');setItems((o.items||[]).map((i:any)=>({...i})));setShow(true)};
 const addItem=()=>{const p=data.products.find((x:any)=>x.id===productId),n=Number(qty);if(!p||!Number.isFinite(n)||n<=0){alert('Select a product and enter a valid quantity.');return}setItems(prev=>[...prev,{productId:p.id,name:p.name,qty:n,price:Number(p.price)||0,tax:Number(p.tax)||0}]);setQty('1')};
 const total=(o:any)=>Number(o.total??(o.items||[]).reduce((a:number,i:any)=>a+(Number(i.qty)||0)*(Number(i.price)||0)*(1+(Number(i.tax)||0)/100),0));
 const persist=()=>{const c=data.customers.find((x:any)=>x.id===customerId);if(!c){alert('Please create a customer before creating a sales order.');return}if(!items.length){alert('Add at least one inventory product.');return}const amount=items.reduce((a:number,i:any)=>a+Number(i.qty)*Number(i.price)*(1+Number(i.tax||0)/100),0);const order={...(editing||{}),id:editing?.id||`SO_${Date.now()}_${Math.random().toString(36).slice(2,8)}`,customerId,customer:c.name,date,status,total:Math.round(amount*100)/100,items,updatedAt:new Date().toISOString(),createdAt:editing?.createdAt||new Date().toISOString()};const orders=editing?data.salesOrders.map((x:any)=>x.id===editing.id?order:x):[order,...data.salesOrders];save({...data,salesOrders:orders,auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:editing?'Updated sales order':'Created sales order',module:'Sales Orders',reference:order.id},...data.auditLogs]});setShow(false)};
 const makeInvoice=(o:any)=>{if(data.invoices.some((i:any)=>i.sourceOrderId===o.id)){alert('An invoice has already been created for this order.');return}const c=data.customers.find((x:any)=>x.id===o.customerId)||data.customers.find((x:any)=>x.name===o.customer);if(!c){alert('The order customer no longer exists.');return}const invoice={id:`${data.invoiceSettings.prefix||'INV-'}${String(data.invoices.length+1).padStart(4,'0')}`,customerId:c.id,customer:c.name,date:new Date().toISOString().slice(0,10),dueDate:new Date().toISOString().slice(0,10),items:(o.items||[]).map((i:any)=>({...i})),subtotal:(o.items||[]).reduce((a:number,i:any)=>a+Number(i.qty)*Number(i.price),0),taxAmount:(o.items||[]).reduce((a:number,i:any)=>a+Number(i.qty)*Number(i.price)*Number(i.tax||0)/100,0),total:total(o),status:'Draft',payment:'Unpaid',paidAmount:0,sourceOrderId:o.id,notes:`Created from Sales Order ${o.id}`,createdAt:new Date().toISOString()};save({...data,invoices:[invoice,...data.invoices],salesOrders:data.salesOrders.map((x:any)=>x.id===o.id?{...x,status:'Confirmed',invoiceId:invoice.id}:x),auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:'Created invoice from sales order',module:'Sales Orders',reference:o.id},...data.auditLogs]});alert(`Invoice ${invoice.id} created as Draft.`)};
 const list=data.salesOrders.filter((o:any)=>(filter==='All'||o.status===filter)&&`${o.id} ${o.customer} ${(o.items||[]).map((i:any)=>i.name).join(' ')}`.toLowerCase().includes(q.toLowerCase()));const orderValue=list.reduce((a:number,o:any)=>a+total(o),0);
 return <><PageHead title="Sales Orders" sub="Create and manage customer orders using existing inventory products." actions={<><button className="btn" onClick={()=>exportCsv('billbook-sales-orders.csv',['Order ID','Customer','Products','Amount','Status','Date'],list.map((o:any)=>[o.id,o.customer,(o.items||[]).map((i:any)=>`${i.name} × ${i.qty}`).join(', '),total(o),o.status,o.date]))}><Icons.Download/>Export</button><button className="btn primary" onClick={reset}><Icons.Plus/>Create Order</button></>}/>
 <div className="grid grid-4"><Stat label="Total Orders" value={data.salesOrders.length}/><Stat label="Pending" value={data.salesOrders.filter((o:any)=>o.status==='Pending').length}/><Stat label="Confirmed" value={data.salesOrders.filter((o:any)=>o.status==='Confirmed').length}/><Stat label="Order Value" value={money(data.salesOrders.reduce((a:number,o:any)=>a+total(o),0))}/></div><div className="filter-pills">{['All','Pending','Confirmed','Processing','Dispatched','Delivered','Cancelled'].map(x=><button key={x} className={filter===x?'filter-active':''} onClick={()=>setFilter(x)}>{x}</button>)}</div><Card className="search-card"><input className="input" placeholder="Search order ID, customer or product..." value={q} onChange={e=>setQ(e.target.value)}/></Card><Card><Table headers={['Order ID','Customer','Products','Amount','Status','Date','Actions']} rows={list.map((o:any)=>[o.id,o.customer,(o.items||[]).map((i:any)=>`${i.name} × ${i.qty}`).join(', ')||'—',money(total(o)),<select className="select" value={o.status||'Pending'} onChange={e=>save({...data,salesOrders:data.salesOrders.map((x:any)=>x.id===o.id?{...x,status:e.target.value,updatedAt:new Date().toISOString()}:x)})}>{['Pending','Confirmed','Processing','Dispatched','Delivered','Cancelled'].map(x=><option key={x}>{x}</option>)}</select>,o.date,<div className="actions"><button className="btn" onClick={()=>edit(o)}>Edit</button>{!o.invoiceId&&!data.invoices.some((i:any)=>i.sourceOrderId===o.id)&&<button className="btn primary" onClick={()=>makeInvoice(o)}>Create Invoice</button>}</div>])}/></Card>
 {show&&<Modal title={editing?`Edit ${editing.id}`:'Create Sales Order'} onClose={()=>setShow(false)} onSave={persist}><div className="grid grid-2"><label className="field"><span>Customer *</span><select className="select" value={customerId} onChange={e=>setCustomerId(e.target.value)}>{data.customers.map((c:any)=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label><label className="field"><span>Order Date</span><input className="input" type="date" value={date} onChange={e=>setDate(e.target.value)}/></label><label className="field"><span>Status</span><select className="select" value={status} onChange={e=>setStatus(e.target.value)}>{['Pending','Confirmed','Processing','Dispatched','Delivered','Cancelled'].map(x=><option key={x}>{x}</option>)}</select></label></div><hr/><div className="grid grid-3"><label className="field"><span>Inventory Product</span><select className="select" value={productId} onChange={e=>setProductId(e.target.value)}>{data.products.map((p:any)=><option key={p.id} value={p.id}>{p.name} — {money(p.price)}</option>)}</select></label><label className="field"><span>Quantity</span><input className="input" type="number" min="1" value={qty} onChange={e=>setQty(e.target.value)}/></label><button className="btn" onClick={addItem} type="button"><Icons.Plus/>Add Item</button></div><Table headers={['Product','Qty','Rate','GST','Line Total','Remove']} rows={items.map((i:any,index:number)=>[i.name,i.qty,money(i.price),`${i.tax||0}%`,money(i.qty*i.price*(1+(i.tax||0)/100)),<button className="btn danger" onClick={()=>setItems(items.filter((_:any,j:number)=>j!==index))}>Remove</button>])}/><div className="row-between"><b>Order Total</b><strong>{money(items.reduce((a:number,i:any)=>a+i.qty*i.price*(1+(i.tax||0)/100),0))}</strong></div></Modal>}</>
}

function Suppliers({data,save}:{data:UserData;save:Save}){
 const [show,setShow]=useState(false),[editing,setEditing]=useState<any>(null),[q,setQ]=useState(''),[view,setView]=useState('Table');
 const [form,setForm]=useState<any>({name:'',phone:'',email:'',gstin:'',address:'',openingBalance:0});
 const open=(supplier?:any)=>{setEditing(supplier||null);setForm(supplier?{name:supplier.name||'',phone:supplier.phone||'',email:supplier.email||'',gstin:supplier.gstin||'',address:supplier.address||'',openingBalance:Number(supplier.openingBalance)||0}:{name:'',phone:'',email:'',gstin:'',address:'',openingBalance:0});setShow(true)};
 const list=data.suppliers.filter((s:any)=>`${s.name} ${s.phone||''} ${s.email||''} ${s.gstin||''}`.toLowerCase().includes(q.toLowerCase()));
 const purchaseTotal=(s:any)=>data.purchases.filter((p:any)=>p.supplierId===s.id||(p.supplierId==null&&p.supplier===s.name)).reduce((a:number,p:any)=>a+Number(p.total||0),0)||Number(s.purchases||0);
 const paidTotal=(s:any)=>data.payments.filter((p:any)=>p.status!=='Voided'&&p.type!=='Receipt'&&(p.supplierId===s.id||p.partyId===s.id||p.party===s.name)).reduce((a:number,p:any)=>a+Number(p.amount||0),0)||Number(s.paid||0);
 const saveForm=()=>{if(!form.name.trim()){alert('Supplier name is required.');return}if(form.email&&!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)){alert('Enter a valid email address.');return}if(form.phone&&!/^[+\d\s()-]{7,18}$/.test(form.phone)){alert('Enter a valid phone number.');return}if(form.gstin&&!/^[0-9A-Z]{15}$/.test(form.gstin.toUpperCase())){alert('GSTIN must contain 15 letters/numbers.');return}const value={...form,name:form.name.trim(),phone:form.phone.trim(),email:form.email.trim(),gstin:form.gstin.trim().toUpperCase(),openingBalance:Number(form.openingBalance)||0};const suppliers=editing?data.suppliers.map((s:any)=>s.id===editing.id?{...s,...value}:s):[{id:uid('supplier'),...value,purchases:0,paid:0,createdAt:new Date().toISOString()},...data.suppliers];save({...data,suppliers,auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:editing?'Updated supplier':'Created supplier',module:'Suppliers',reference:editing?.id||form.name},...data.auditLogs]});setShow(false)};
 const purchases=list.reduce((a:number,s:any)=>a+purchaseTotal(s),0),paid=list.reduce((a:number,s:any)=>a+paidTotal(s),0);
 return <><PageHead title="Suppliers" sub="Supplier details and balances use the existing business records." actions={<><button className="btn" onClick={()=>exportCsv('billbook-suppliers.csv',['Supplier','Mobile','Email','GSTIN','Purchases','Paid','Outstanding'],list.map((s:any)=>[s.name,s.phone||'',s.email||'',s.gstin||'',purchaseTotal(s),paidTotal(s),Math.max(0,purchaseTotal(s)+Number(s.openingBalance||0)-paidTotal(s))]))}><Icons.Download/>Export</button><button className="btn primary" onClick={()=>open()}><Icons.UserPlus/>New Supplier</button></>}/>
 <div className="grid grid-4"><Stat label="Total Suppliers" value={data.suppliers.length}/><Stat label="Total Purchases" value={money(data.suppliers.reduce((a:number,s:any)=>a+purchaseTotal(s),0))}/><Stat label="Amount Paid" value={money(data.suppliers.reduce((a:number,s:any)=>a+paidTotal(s),0))}/><Stat label="Outstanding Payables" value={money(data.suppliers.reduce((a:number,s:any)=>a+Math.max(0,purchaseTotal(s)+Number(s.openingBalance||0)-paidTotal(s)),0))}/></div>
 <Card className="search-card"><div className="row-between"><input className="input" placeholder="Search supplier, phone, email or GSTIN..." value={q} onChange={e=>setQ(e.target.value)}/><div className="actions">{['Table','Kanban'].map(v=><button key={v} className={`btn ${view===v?'primary':''}`} onClick={()=>setView(v)}>{v}</button>)}</div></div><p className="muted small">Showing {list.length} of {data.suppliers.length} suppliers · Purchases {money(purchases)} · Paid {money(paid)}</p></Card>
 {view==='Table'?<Card><Table headers={['Supplier','Mobile','GSTIN','Purchases','Paid','Outstanding','Actions']} rows={list.map((s:any)=>[s.name,s.phone||'—',s.gstin||'—',money(purchaseTotal(s)),money(paidTotal(s)),money(Math.max(0,purchaseTotal(s)+Number(s.openingBalance||0)-paidTotal(s))),<button className="btn" onClick={()=>open(s)}>Edit</button>])}/></Card>:<div className="grid grid-3">{list.map((s:any)=><Card key={s.id}><div className="section-head"><h3>{s.name}</h3><button className="btn" onClick={()=>open(s)}>Edit</button></div><p className="muted small">{s.phone||'No phone'} · {s.gstin||'No GSTIN'}</p><p>Purchases <b>{money(purchaseTotal(s))}</b></p><p>Paid <b>{money(paidTotal(s))}</b></p><p>Outstanding <b>{money(Math.max(0,purchaseTotal(s)+Number(s.openingBalance||0)-paidTotal(s)))}</b></p></Card>)}</div>}
 {show&&<Modal title={editing?`Edit Supplier · ${editing.name}`:'New Supplier'} onClose={()=>setShow(false)} onSave={saveForm}><div className="grid grid-2">{[['name','Supplier Name *'],['phone','Mobile'],['email','Email'],['gstin','GSTIN'],['address','Address']].map(([key,label])=><label className="field" key={key}><span>{label}</span><input className="input" value={form[key]||''} onChange={e=>setForm({...form,[key]:e.target.value})}/></label>)}<label className="field"><span>Opening Balance (₹)</span><input className="input" type="number" min="0" step="0.01" value={form.openingBalance} onChange={e=>setForm({...form,openingBalance:e.target.value})}/></label></div></Modal>}</>}

function Products({data,save}: {data:UserData;save:Save}){const [show,setShow]=useState(false),[name,setName]=useState(''),[price,setPrice]=useState('0'),[stock,setStock]=useState('0');return <><PageHead title="Products & Inventory" sub="Products, stock, barcode and batch data stay inside the active business." actions={<button className="btn primary" onClick={()=>setShow(true)}><Icons.Plus/>Add Product</button>}/><div className="grid grid-3"><Stat label="Products" value={data.products.length}/><Stat label="Units in Stock" value={data.products.reduce((a,p)=>a+p.stock,0)}/><Stat label="Low Stock" value={data.products.filter(p=>p.stock<=p.reorder).length}/></div><Card><Table headers={['Product','SKU','Barcode','Category','Stock','Rate','GST','Status']} rows={data.products.map(p=>[p.name,p.sku,p.barcode,p.category,p.stock,money(p.price),`${p.tax}%`,<Badge text={p.stock<=p.reorder?'Low Stock':'In Stock'}/>])}/></Card>{show&&<Modal title="Add Product" onClose={()=>setShow(false)} onSave={()=>{if(!name.trim())return;const p={id:uid('product'),name:name.trim(),sku:`SKU-${Date.now()}`,barcode:`890${Date.now().toString().slice(-10)}`,category:'General',hsnSac:'',purchasePrice:0,price:Number(price)||0,tax:18,unit:'pcs',openingStock:Number(stock)||0,stock:Number(stock)||0,minimumStock:0,reorder:5};save({...data,products:[p,...data.products],categories:data.categories.includes('General')?data.categories:[...data.categories,'General']});setShow(false);setName('');setPrice('0');setStock('0')}}><label className="label">Product Name</label><input className="input" value={name} onChange={e=>setName(e.target.value)}/><label className="label">Selling Price</label><input className="input" type="number" value={price} onChange={e=>setPrice(e.target.value)}/><label className="label">Opening Stock</label><input className="input" type="number" value={stock} onChange={e=>setStock(e.target.value)}/></Modal>}</>}

function Batches({data}:{data:UserData}){return <><PageHead title="Batch & Expiry" sub="Inventory expiry tracking is demo/localStorage based."/><Card><Table headers={['Product','Batch','Mfg Date','Expiry Date','Qty','Rate','Status']} rows={data.batches.map(b=>[data.products.find(p=>p.id===b.productId)?.name||'—',b.batchNo,b.manufacturingDate,b.expiryDate,b.quantity,money(b.saleRate),<Badge text={b.status}/>])}/></Card></>}
function Barcode({data,save}: {data:UserData;save:Save}){const [product,setProduct]=useState(data.products[0]?.id||'');const p=data.products.find(x=>x.id===product);return <><PageHead title="Barcode Management" sub="Generate, search and preview demo barcode labels."/><div className="grid grid-2"><Card className="panel"><label className="label">Product</label><select className="select" value={product} onChange={e=>setProduct(e.target.value)}>{data.products.map(x=><option value={x.id} key={x.id}>{x.name}</option>)}</select><label className="label">Barcode</label><input className="input" value={p?.barcode||''} readOnly/><div className="actions"><button className="btn primary" onClick={()=>save({...data,barcodeData:[...data.barcodeData,{productId:product,barcode:p?.barcode,generatedAt:new Date().toISOString()}]})}>Generate</button><button className="btn" onClick={()=>window.print()}>Print Labels</button></div></Card><Card className="barcode-preview"><h3>{p?.name}</h3><div className="barcode-lines">{Array.from({length:48},(_,i)=><i key={i} style={{width:i%5===0?3:1}}/>)}</div><b>{p?.barcode}</b></Card></div></>}

function Ledger({data,customer}:{data:UserData;customer?:boolean}){const list=customer?data.customers:data.suppliers;const rows=list.flatMap((p:any)=>{const total=customer?p.sales:p.purchases,paid=customer?p.received:p.paid,opening=p.openingBalance||0;return [[p.name,money(opening),customer?money(total):money(total),money(paid),money(total-paid),customer?`Debit ${money(total)}`:`Credit ${money(total)}`]]});return <><PageHead title={customer?'Customer Ledger':'Supplier Ledger'} sub="One ledger calculation is reused by Parties and Accounting." actions={<button className="btn" onClick={()=>exportCsv(`${customer?'customer':'supplier'}-ledger.csv`,['Party','Opening','Sales/Purchases','Received/Paid','Outstanding','Particular'],rows.map(r=>r.map(String)))}><Icons.Download/>Download Statement</button>}/><Card><Table headers={['Party','Opening Balance','Sales / Purchases','Received / Paid','Outstanding','Particular']} rows={rows}/></Card></>}

function Reminders({data}:{data:UserData}){return <><PageHead title="Payment Reminders" sub="Demo actions only — no WhatsApp, SMS or email is sent."/><div className="grid grid-3"><Stat label="Outstanding" value={money(totals(data).receivables)}/><Stat label="Pending Invoices" value={data.reminders.length}/><Stat label="Demo Message" value="Not Sent"/></div><Card><Table headers={['Customer','Invoice','Due Date','Amount','Status','Action']} rows={data.reminders.map(r=>[r.customer,r.invoice,r.dueDate,money(r.amount),<Badge text={r.status}/>,<button className="btn" onClick={()=>alert('Demo reminder confirmation — nothing was sent.')}>Send Demo Reminder</button>])}/></Card></>}

function AccountingPage({data,kind}:{data:UserData;kind:string}){const gl=buildGeneralLedger(data);if(kind==='General Ledger')return <><PageHead title="General Ledger" sub="Calculated from invoices, payments, purchases, expenses and income."/><Card><Table headers={['Date','Account','Particular','Debit','Credit','Ref']} rows={gl.map(x=>[x.date,x.account,x.particular,money(x.debit),money(x.credit),x.reference||'—'])}/></Card></>;if(kind==='Journal Entries')return <><PageHead title="Journal Entries"/><Card><Table headers={['Date','Reference','Narration','Debit','Credit']} rows={buildJournals(data).map(j=>[j.date,j.reference,j.narration,money(j.lines.reduce((a,l)=>a+l.debit,0)),money(j.lines.reduce((a,l)=>a+l.credit,0))])}/></Card></>;if(kind==='Trial Balance'){const map:Record<string,{d:number;c:number}>={};gl.forEach(x=>{map[x.account]??={d:0,c:0};map[x.account].d+=x.debit;map[x.account].c+=x.credit});return <><PageHead title="Trial Balance"/><Card><Table headers={['Account','Debit','Credit']} rows={Object.entries(map).map(([a,v])=>[a,money(v.d),money(v.c)])}/></Card></>};const filter=kind==='Cash Book'?'Cash/Bank':'Cash/Bank';const rows=gl.filter(x=>x.account===filter||x.particular.toLowerCase().includes('cash')).map(x=>[x.date,x.particular,money(x.debit),money(x.credit),x.reference||'—']);return <><PageHead title={kind}/><Card><Table headers={['Date','Particular','Debit','Credit','Reference']} rows={rows}/></Card></>}

function Expenses({data,save}: {data:UserData;save:Save}){const [show,setShow]=useState(false),[category,setCategory]=useState('General'),[amount,setAmount]=useState('0');return <><PageHead title="Expenses" sub="Expenses flow into dashboard, Profit & Loss, Cash/Bank, Cash Flow and Day Book." actions={<button className="btn primary" onClick={()=>setShow(true)}><Icons.Plus/>New Expense</button>}/><Card><Table headers={['ID','Category','Amount','Mode','Vendor','Date']} rows={data.expenses.map(e=>[e.id,e.category,money(e.amount),e.mode,e.vendor,e.date])}/></Card>{show&&<Modal title="New Expense" onClose={()=>setShow(false)} onSave={()=>{const a=Number(amount)||0;if(!a)return;const e={id:uid('expense'),category,vendor:'Demo Vendor',amount:a,date:today(),mode:'Cash',description:'Demo expense'};save({...data,expenses:[e,...data.expenses],cashBalance:Math.max(0,data.cashBalance-a),auditLogs:[{id:uid('audit'),date:today(),time:new Date().toLocaleTimeString(),user:data.businessProfile.ownerName,action:'Created expense',module:'Expenses',reference:e.id},...data.auditLogs]});setShow(false);setAmount('0')}}><label className="label">Category</label><input className="input" value={category} onChange={e=>setCategory(e.target.value)}/><label className="label">Amount</label><input className="input" type="number" value={amount} onChange={e=>setAmount(e.target.value)}/></Modal>}</>}

function GenericPage({data,save}:{data:UserData;save:Save}){const path=useLocation().pathname;const parts=path.split('/').filter(Boolean);const title=(parts.at(-1)||'Dashboard').replaceAll('-',' ').replace(/\b\w/g,c=>c.toUpperCase());const [q,setQ]=useState('');const rows=useMemo(()=>{if(path.includes('supplier'))return data.suppliers.map(s=>[s.name,s.phone,money(s.purchases),money(s.paid),money(s.purchases-s.paid)]);if(path.includes('customer'))return data.customers.map(c=>[c.name,c.phone,money(c.sales),money(c.received),money(c.sales-c.received)]);if(path.includes('product')||path.includes('stock'))return data.products.map(p=>[p.name,p.sku,p.barcode,p.stock,money(p.price)]);if(path.includes('payment')||path.includes('receipts'))return data.payments.map(p=>[p.id,p.party,p.type,money(p.amount),p.mode,p.date]);if(path.includes('expense'))return data.expenses.map(e=>[e.id,e.category,money(e.amount),e.mode,e.date]);if(path.includes('purchase'))return data.purchases.map(p=>[p.id,p.supplier,money(p.total),p.status,p.date]);if(path.includes('order'))return data.salesOrders.map(o=>[o.id,o.customer,money(o.total),o.status,o.date]);if(path.includes('quotation'))return data.quotations.map(o=>[o.id,o.customer,money(o.total),o.status,o.date]);if(path.includes('notification'))return data.notifications.map(n=>[n.text,n.read?'Read':'Unread']);if(path.includes('report')||path.includes('gst'))return data.invoices.map(i=>[i.id,i.customer,money(i.total),i.status,i.date]);return data.invoices.map(i=>[i.id,i.customer,money(i.total),i.status,i.date])},[path,data]);const filtered=rows.filter(r=>r.join(' ').toLowerCase().includes(q.toLowerCase()));const headers=rows[0]?.length===6?['Reference','Party','Type/Amount','Amount','Mode','Date']:rows[0]?.length===5?['Reference','Party','Amount','Status','Date']:rows[0]?.length===2?['Message','State']:['Name','Contact','Sales/Purchases','Paid/Received','Outstanding'];return <><PageHead title={title} sub="Functional frontend demo screen using current business data." actions={<button className="btn" onClick={()=>exportCsv(`${title.toLowerCase().replaceAll(' ','-')}.csv`,headers,filtered.map(r=>r.map(String)))}><Icons.Download/>Export</button>}/><Card className="search-card"><input className="input" placeholder={`Search ${title}...`} value={q} onChange={e=>setQ(e.target.value)}/></Card><Card><Table headers={headers} rows={filtered}/></Card></>}

function CalendarPage({data,save}:{data:UserData;save:Save}){const [show,setShow]=useState(false),[title,setTitle]=useState(''),[month,setMonth]=useState(new Date(2026,8,1));const days=new Date(month.getFullYear(),month.getMonth()+1,0).getDate();return <><PageHead title="Calendar" actions={<><button className="btn" onClick={()=>setMonth(new Date(month.getFullYear(),month.getMonth()-1,1))}>Previous</button><button className="btn" onClick={()=>setMonth(new Date(2026,8,1))}>Today</button><button className="btn" onClick={()=>setMonth(new Date(month.getFullYear(),month.getMonth()+1,1))}>Next</button><button className="btn primary" onClick={()=>setShow(true)}>Add Event</button></>}/><Card><div className="calendar-grid">{Array.from({length:days},(_,i)=>{const date=`${month.getFullYear()}-${String(month.getMonth()+1).padStart(2,'0')}-${String(i+1).padStart(2,'0')}`;const ev=data.calendarEvents.filter(e=>e.date===date);return <div className="day-cell" key={date}><b>{i+1}</b>{ev.map(e=><div className="event" key={e.id}>{e.title}</div>)}</div>})}</div></Card>{show&&<Modal title="Create Calendar Event" onClose={()=>setShow(false)} onSave={()=>{if(!title.trim())return;save({...data,calendarEvents:[...data.calendarEvents,{id:uid('event'),title,date:today().split(' ').reverse().join('-'),time:'10:00',description:'',type:'General'}]});setTitle('');setShow(false)}}><label className="label">Title</label><input className="input" value={title} onChange={e=>setTitle(e.target.value)}/></Modal>}</>}

function Calculator(){const [v,setV]=useState('');const keys=['7','8','9','÷','4','5','6','×','1','2','3','-','0','.','%','+','AC','=','⌫'];const evaluate=()=>{try{return String(Function(`return ${v.replaceAll('×','*').replaceAll('÷','/')}`)())}catch{return v}};return <><PageHead title="Business Calculator"/><Card className="calc"><input className="calc-display" value={v} readOnly/>{keys.map(k=><button className={`btn ${['=','AC'].includes(k)?'primary':''}`} key={k} onClick={()=>k==='AC'?setV(''):k==='⌫'?setV(v.slice(0,-1)):k==='='?setV(evaluate()):setV(v+k)}>{k}</button>)}</Card></>}

function Backup({data,save,userId}:{data:UserData;save:Save;userId:string}){const ref=useRef<HTMLInputElement>(null);const [preview,setPreview]=useState<string>('');const download=(kind:string)=>{const d=kind==='full'?data:kind==='customers'?{customers:data.customers}:kind==='products'?{products:data.products}:kind==='invoices'?{invoices:data.invoices}:{payments:data.payments};const blob=new Blob([JSON.stringify({version:2,userId,exportedAt:new Date().toISOString(),data:d},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`billbooks-${kind}-backup.json`;a.click()};const importFile=(f:File)=>{const r=new FileReader();r.onload=()=>{try{const parsed=JSON.parse(String(r.result));if(parsed.userId!==userId)throw new Error('This backup belongs to another BillBooks user.');setPreview(JSON.stringify(parsed,null,2));(window as any).__bbBackup=parsed}catch(e){alert((e as Error).message)}};r.readAsText(f)};const restore=(mode:'merge'|'replace')=>{const b=(window as any).__bbBackup as BackupData|undefined;if(!b)return;const incoming=b.data as Partial<UserData>;if(mode==='replace'){save({...data,...incoming});}else{const arrayKeys=['customers','suppliers','products','invoices','payments','expenses','purchases','quotations','salesOrders','returns','ledgers','generalLedger','journalEntries','salesLedger','reminders','batches','barcodeData','heldBills','posBills','auditLogs','notifications','otherIncome','creditNotes','debitNotes','calendarEvents','recurringInvoices','expenseClaims'] as const;const merged=clone(data);arrayKeys.forEach(k=>{const incomingArr=(incoming as any)[k];if(Array.isArray(incomingArr))(merged as any)[k]=[...(merged as any)[k],...incomingArr.filter((x:any)=>!(merged as any)[k].some((y:any)=>y.id&&x.id&&y.id===x.id))]});save(merged)}setPreview('');alert(`Backup ${mode} restored for the active user.`)};return <><PageHead title="Backup / Restore" sub="Backup contains data for the currently active BillBooks business."/><div className="grid grid-2"><Card className="panel"><h3>Backup</h3><p className="muted small">Only current-user data is exported. Session credentials are never included.</p><div className="backup-grid">{[['Full Backup','full'],['Customers','customers'],['Products','products'],['Invoices','invoices'],['Payments','payments']].map(([x,k])=><button className="btn" key={k} onClick={()=>download(k)}><Icons.Download/>Export {x}</button>)}</div></Card><Card className="panel"><h3>Restore</h3><input ref={ref} type="file" accept=".json,application/json" hidden onChange={e=>e.target.files?.[0]&&importFile(e.target.files[0])}/><button className="btn primary" onClick={()=>ref.current?.click()}><Icons.Upload/>Import Backup</button>{preview&&<><div className="preview-box">{preview.slice(0,1800)}{preview.length>1800?'…':''}</div><div className="actions"><button className="btn" onClick={()=>restore('merge')}>Merge Data</button><button className="btn primary" onClick={()=>restore('replace')}>Replace Data</button><button className="btn" onClick={()=>{setPreview('');(window as any).__bbBackup=undefined}}>Cancel</button></div></>}</Card></div></>}

function DriveSettings({data,user,save}:{data:UserData;user:User;save:Save}){
 const [clientId,setClientId]=useState(import.meta.env.VITE_GOOGLE_CLIENT_ID||'');
 const [status,setStatus]=useState('Not connected');
 const [email,setEmail]=useState('');
 const [requestedEmail,setRequestedEmail]=useState('');
 const [folderId,setFolderId]=useState('');
 const [fileId,setFileId]=useState('');
 const [token,setToken]=useState('');
 const [lastSync,setLastSync]=useState('');
 const [busy,setBusy]=useState(false);
 const [message,setMessage]=useState('');
 const [initialized,setInitialized]=useState(false);
 const tokenClient=useRef<any>(null);
 const lastSavedPayload=useRef('');
 const driveApi='https://www.googleapis.com/drive/v3';
 const uploadApi='https://www.googleapis.com/upload/drive/v3/files';
const call = useCallback(
  async (url: string, init: RequestInit = {}) => {
    const r = await fetch(url, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(init.headers || {}),
      },
    });

    if (!r.ok) {
      const detail = await r.text().catch(() => '');

      throw new Error(
        r.status === 401
          ? 'Google authorization expired. Reconnect Google Drive and retry.'
          : r.status === 403
            ? 'Google Drive denied the requested permission. Check Drive API and OAuth consent configuration.'
            : `Google Drive request failed (${r.status}). ${detail.slice(0, 180)}`
      );
    }

    return r.status === 204 ? null : r.json();
  },
  [token]
);
 const connect=()=>{
   const normalized=requestedEmail.trim().toLowerCase();
   if(!clientId.trim()){setMessage('Configure the Google OAuth Web Client ID in VITE_GOOGLE_CLIENT_ID or the field above.');return}
   if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized)){setMessage('Enter a valid Google account email address.');return}
   setMessage('');setBusy(true);setStatus('Connecting');
   const w=window as any;
   const start=()=>{
     try{
       tokenClient.current=w.google.accounts.oauth2.initTokenClient({
         client_id:clientId.trim(),
         scope:'https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/userinfo.email',
         callback:async(resp:any)=>{
           if(resp.error||!resp.access_token){setStatus('Connection failed');setMessage(resp.error_description||resp.error||'Google authorization was not completed.');setBusy(false);return}
           try{
             const me=await fetch('https://www.googleapis.com/oauth2/v3/userinfo',{headers:{Authorization:`Bearer ${resp.access_token}`}});
             if(!me.ok)throw new Error('Google could not verify the authorized account. Please retry.');
             const who=await me.json();
             const verified=String(who.email||'').trim().toLowerCase();
             if(!verified||who.email_verified!==true)throw new Error('Google did not return a verified email address.');
             if(verified!==normalized){setStatus('Not connected');setMessage(`The Google account selected (${verified}) does not match ${normalized}. Retry and choose the intended Google account.`);setBusy(false);return}
             setToken(resp.access_token);setEmail(verified);setStatus('Connected');setInitialized(false);
             setMessage('Google account verified. Choose Sync Now to create or update your business snapshot, or Restore from Drive to load an existing snapshot.');
           }catch(e){setStatus('Connection failed');setMessage((e as Error).message||'Could not verify the Google account.');}
           finally{setBusy(false)}
         },
         error_callback:(e:any)=>{setStatus('Connection failed');setMessage(e.message||'Google authorization popup failed or was cancelled.');setBusy(false)}
       });
       tokenClient.current.requestAccessToken({prompt:'select_account',login_hint:normalized});
     }catch(e){setStatus('Connection failed');setMessage((e as Error).message||'Could not start Google authorization.');setBusy(false)}
   };
   if(w.google?.accounts?.oauth2){start();return}
   const script=document.createElement('script');script.src='https://accounts.google.com/gsi/client';
   script.onload=start;script.onerror=()=>{setStatus('Connection failed');setMessage('Could not load Google Identity Services. Check your network and retry.');setBusy(false)};
   document.head.appendChild(script);
 };
 const sync = useCallback(async () => {
  if (!token) {
    setMessage('Connect and verify Google Drive first.');
    return;
  }

  setBusy(true);
  setStatus('Syncing');
  setMessage('');

  try {
    let folder = folderId;
    const folderName = `NTAXCO My BillBook - ${user.id}`;

    if (!folder) {
      const q = await call(
        `${driveApi}/files?q=${encodeURIComponent(
          `name='${folderName.replace(/'/g, "\\'")}' and mimeType='application/vnd.google-apps.folder' and trashed=false`
        )}&fields=files(id,name)&pageSize=100`
      );

      folder = q.files?.[0]?.id;

      if (!folder) {
        const made = await call(
          `${driveApi}/files?fields=id,name`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              name: folderName,
              mimeType: 'application/vnd.google-apps.folder',
              appProperties: {
                ntaxcoBusinessId: String(user.id),
                ntaxcoApp: 'my-billbook',
              },
            }),
          }
        );

        folder = made.id;
      }

      if (!folder) {
        throw new Error('Google Drive did not return a folder ID.');
      }

      setFolderId(folder);
    }

    let target = fileId;

    if (!target) {
      const q = await call(
        `${driveApi}/files?q=${encodeURIComponent(
          `'${folder}' in parents and name='business-${user.id}.json' and trashed=false`
        )}&fields=files(id,name,modifiedTime,version,webViewLink)&pageSize=100`
      );

      target = q.files?.[0]?.id;
    }

    const payload = JSON.stringify(
      {
        schemaVersion: 1,
        business_id: String(user.id),
        businessName:
          data.businessProfile?.businessName || user.name,
        exportedAt: new Date().toISOString(),
        data,
      },
      null,
      2
    );

    const boundary = 'bb_' + Math.random().toString(36).slice(2);

    const meta = JSON.stringify(
      target
        ? {
            name: `business-${user.id}.json`,
            appProperties: {
              ntaxcoBusinessId: String(user.id),
              ntaxcoApp: 'my-billbook',
            },
          }
        : {
            name: `business-${user.id}.json`,
            mimeType: 'application/json',
            parents: [folder],
            appProperties: {
              ntaxcoBusinessId: String(user.id),
              ntaxcoApp: 'my-billbook',
            },
          }
    );

    const body =
      `--${boundary}\r\n` +
      `Content-Type: application/json; charset=UTF-8\r\n\r\n` +
      `${meta}\r\n` +
      `--${boundary}\r\n` +
      `Content-Type: application/json\r\n\r\n` +
      `${payload}\r\n` +
      `--${boundary}--`;

    const saved = await call(
      `${uploadApi}${target ? '/' + target : ''}?uploadType=multipart&fields=id,name,modifiedTime,version,webViewLink`,
      {
        method: target ? 'PATCH' : 'POST',
        headers: {
          'Content-Type': `multipart/related; boundary=${boundary}`,
        },
        body,
      }
    );

    if (!saved?.id) {
      throw new Error('Google Drive did not confirm the snapshot write.');
    }

    setFileId(saved.id);
    setLastSync(new Date().toLocaleString());
    lastSavedPayload.current = JSON.stringify(data);
    setInitialized(true);
    setStatus('Saved to Google Drive');

    setMessage(
      `Google Drive confirmed the snapshot write (${saved.name}). Automatic sync is active for changes in this open workspace. Keep this page open and reconnect after refresh if Google authorization expires.`
    );
  } catch (e) {
    setStatus(
      String((e as Error).message).includes('expired')
        ? 'Reauthorization required'
        : 'Sync failed'
    );

    setMessage(
      (e as Error).message ||
        'Google Drive synchronization failed. Your current workspace has not been discarded. Retry when the connection is available.'
    );
  } finally {
    setBusy(false);
  }
}, [
  token,
  folderId,
  fileId,
  user.id,
  user.name,
  data,
  call,
  driveApi,
  uploadApi,
]);
useEffect(() => {
  if (!token || !initialized || busy) return;

  const serialized = JSON.stringify(data);

  if (!serialized || serialized === lastSavedPayload.current) {
    return;
  }

  const timer = window.setTimeout(() => {
    void sync();
  }, 1200);

  return () => window.clearTimeout(timer);
}, [data, token, initialized, busy, sync]);
 const restoreFromDrive=async()=>{
   if(!token){setMessage('Connect and verify Google Drive first.');return}
   setBusy(true);setStatus('Loading business data');setMessage('');
   try{
     let folder=folderId;
     if(!folder){
       const folderName=`NTAXCO My BillBook - ${user.id}`;
       const qf=await call(`${driveApi}/files?q=${encodeURIComponent(`name='${folderName}' and mimeType='application/vnd.google-apps.folder' and trashed=false`)}&fields=files(id,name)&pageSize=100`);
       folder=qf.files?.[0]?.id;
       if(folder)setFolderId(folder);
     }
     if(!folder)throw new Error('No NTAXCO My BillBook folder was found in this Google account.');
     let target=fileId;
     if(!target){const q=await call(`${driveApi}/files?q=${encodeURIComponent(`'${folder}' in parents and name='business-${user.id}.json' and trashed=false`)}&fields=files(id,name,modifiedTime)&pageSize=100`);target=q.files?.[0]?.id}
     if(!target)throw new Error('No My BillBook snapshot was found in this folder. Use Sync Now to create the first snapshot.');
     const response=await fetch(`${driveApi}/files/${target}?alt=media`,{headers:{Authorization:`Bearer ${token}`}});
     if(!response.ok)throw new Error(response.status===401?'Google authorization expired. Reconnect and retry.':`Could not download the Drive snapshot (${response.status}).`);
     const snapshot=await response.json();
     if(String(snapshot.business_id)!==String(user.id))throw new Error('This Drive snapshot belongs to a different NTAXCO business. Restore was blocked.');
     if(!snapshot.data||!Array.isArray(snapshot.data.invoices)||!Array.isArray(snapshot.data.products))throw new Error('The Drive snapshot format is invalid or incomplete. Current data was not changed.');
     if(!window.confirm(`Restore snapshot ${target} from Google Drive? This replaces the current workspace data. Make sure current changes are saved first.`))return;
     save(snapshot.data as UserData);setFileId(target);setLastSync(new Date().toLocaleString());lastSavedPayload.current=JSON.stringify(snapshot.data);setInitialized(true);setStatus('Saved to Google Drive');setMessage('Snapshot restored and validated. Verify your records before continuing.');
   }catch(e){setStatus('Restore failed');setMessage((e as Error).message||'Restore failed. Current workspace data was retained.')}
   finally{setBusy(false)}
 };
 const disconnect=()=>{
   if(!window.confirm('Disconnect Google Drive? Automatic synchronization will stop. Files in Google Drive will not be deleted.'))return;
   if(token)fetch('https://oauth2.googleapis.com/revoke',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`token=${encodeURIComponent(token)}`}).catch(()=>{});
   setToken('');setEmail('');setFolderId('');setFileId('');setLastSync('');setInitialized(false);lastSavedPayload.current='';setStatus('Not connected');setMessage('Disconnected. Your Google Drive files remain untouched. Reconnect before syncing or restoring again.');
 };
 return <>
   <PageHead title="My Drive" sub="Connect your Google account to store and restore your My BillBook business snapshot."/>
   <Card className="panel">
     <div className="drive-status"><span className={`status-dot ${token?'connected':''}`}/><div><h3>{status}</h3><p className="muted small">{email||'No Google account connected'}</p></div></div>
     {!token&&<>
       <h3>Connect Your Google Drive</h3>
       <p className="muted small">Connect your Google account to securely store and restore your My BillBook business data.</p>
       <label className="label">Google account email</label>
       <input className="input" type="email" autoComplete="email" value={requestedEmail} onChange={e=>setRequestedEmail(e.target.value)} placeholder="you@example.com"/>
       <label className="label">Google OAuth Web Client ID</label>
       <input className="input" value={clientId} onChange={e=>setClientId(e.target.value)} placeholder="xxxxx.apps.googleusercontent.com"/>
       <p className="muted small">Your email is used as an account-selection hint only. Google must authenticate you and grant Drive access. NTAXCO verifies the email returned by Google; entering an email alone never grants access.</p>
       <p className="muted small">Use a Web OAuth client configured with this website's authorized origin and enable the Google Drive API. Never enter a client secret here.</p>
     </>}
     {token&&<p className="muted small">Connected account verified by Google. Access is limited to files available to this app under the granted Drive permission. Access tokens are held in page memory and may require reauthorization after refresh.</p>}
     <div className="actions">
       <button className="btn primary" disabled={busy||!!token} onClick={connect}>{busy?'Connecting…':'Connect Google Drive'}</button>
       <button className="btn" disabled={busy||!token} onClick={sync}>{busy?'Working…':'Sync Now'}</button>
       <button className="btn" disabled={busy||!token} onClick={restoreFromDrive}>Restore Backup</button>
       <button className="btn" disabled={busy||!token||!folderId} onClick={()=>window.open(`https://drive.google.com/drive/folders/${folderId}`,'_blank','noopener,noreferrer')}>Open Google Drive Folder</button>
       <button className="btn" disabled={busy||!token} onClick={disconnect}>Disconnect</button>
     </div>
     <div className="drive-meta"><span>Business folder</span><b>{folderId?'Created / found':'Not initialized'}</b><span>Last successful synchronization</span><b>{lastSync||'Never'}</b><span>Data file</span><b>{fileId?`business-${user.id}.json`:'Not created'}</b></div>
     {message&&<p className="drive-message" role="status">{message}</p>}
   </Card>
   <Card className="panel">
     <h3>Storage and recovery notes</h3>
     <p>After a snapshot is initialized, edits in this open workspace are queued for a debounced Drive snapshot write. A successful status is shown only after Google Drive confirms the write. Failed writes show an error and can be retried with Sync Now.</p>
     <p><b>Production limitation:</b> the rest of this project still uses the existing authenticated <code>/billbook/data</code> backend path and local workspace state. This change does not yet make Drive the sole authoritative repository for every transaction, does not implement server-side token storage or cross-device conflict resolution, and does not replace full production security review. Do not treat this archive as production-ready financial record storage until those items are completed and tested.</p>
   </Card>
 </>;
}
function SettingsPage({title,data,save,section}:{title:string;data:UserData;save:Save;section:'business'|'invoice'}){const [form,setForm]=useState(section==='business'?data.businessProfile:data.invoiceSettings);return <><PageHead title={title} sub="Changes are stored for the current business only."/><Card className="form-card"><div className="grid grid-2">{Object.entries(form).filter(([k])=>!['logo','upiQr','signature'].includes(k)).map(([k,v])=><label className="field" key={k}><span>{k.replaceAll(/([A-Z])/g,' $1')}</span><input className="input" value={String(v??'')} onChange={e=>setForm({...form,[k]:e.target.value})}/></label>)}</div><label className="field"><span>Company Logo URL or data URL</span><input className="input" value={String((form as any).logo||'')} onChange={e=>setForm({...form,logo:e.target.value} as any)} placeholder="https://... or data:image/..."/></label>{(form as any).logo&&<div className="invoice-company"><img src={(form as any).logo} alt="Company logo preview" style={{maxWidth:160,maxHeight:100,objectFit:'contain'}}/><span className="muted small">Invoice logo preview</span></div>}<div className="actions end"><button className="btn primary" onClick={()=>save({...data,[section==='business'?'businessProfile':'invoiceSettings']:form as any})}>Save Changes</button></div></Card></>}

function Permissions({data,save}:{data:UserData;save:Save}){const [role,setRole]=useState<Role>(data.permissions.role);return <><PageHead title="Users & Permissions" sub="Frontend demo permissions; no backend identity provider is connected."/><Card className="form-card"><label className="label">Business Role</label><select className="select" value={role} onChange={e=>setRole(e.target.value as Role)}>{['Business Owner','Administrator','Manager','Sales Person','Accountant','Inventory Manager','Cashier'].map(x=><option key={x}>{x}</option>)}</select><div className="permission-list">{Object.entries(data.permissions.permissions).map(([k,v])=><label key={k}><input type="checkbox" checked={v} onChange={()=>save({...data,permissions:{...data.permissions,role,permissions:{...data.permissions.permissions,[k]:!v}}})}/>{k}</label>)}</div><button className="btn primary" onClick={()=>save({...data,permissions:{...data.permissions,role}})}>Save Role</button></Card></>}

function Audit({data}:{data:UserData}){return <><PageHead title="Activity / Audit Log"/><Card><Table headers={['Date','Time','User','Action','Module','Reference']} rows={data.auditLogs.map(a=>[a.date,a.time,a.user,a.action,a.module,a.reference||'—'])}/></Card></>}
function Profile({user}:{user:User}){const n=useNavigate();return <><PageHead title="My Profile" sub="Local demo identity"/><Card className="profile-card"><div className="big-avatar">{user.name.slice(0,1).toUpperCase()}</div><h2>{user.name}</h2><p className="muted">{user.mobile} • {user.role}</p><p>Created {new Date(user.createdAt).toLocaleDateString('en-IN')}</p><button className="btn" onClick={()=>{logoutNtaxco()}}><Icons.LogOut/>Logout of NTAXCO</button><button className="btn" onClick={()=>n('/profile/history')}>Login History</button></Card></>}
function LoginHistory({user}:{user:User}){return <><PageHead title="Login History"/><Card><Table headers={['Login','Logout','Mobile','Browser','Status']} rows={user.loginHistory.map(x=>[new Date(x.loginAt).toLocaleString('en-IN'),x.logoutAt?new Date(x.logoutAt).toLocaleString('en-IN'):'Active',x.mobile,x.browser,x.status])}/></Card></>}
function Notifications({data,save}:{data:UserData;save:Save}){return <><PageHead title="Notifications" sub="Notifications are stored per active business." actions={<button className="btn" onClick={()=>save({...data,notifications:data.notifications.map(n=>({...n,read:true}))})}>Mark all read</button>}/><Card>{data.notifications.map(n=><div className="notification-row" key={n.id}><span className="notification-icon"><Icons.Bell size={17}/></span><div><b>{n.text}</b><div className="muted small">{n.read?'Read':'Unread'}</div></div></div>)}</Card></>}

function Stat({label,value}:{label:string;value:string|number}){return <Card className="stat-card"><span className="muted small">{label}</span><strong>{value}</strong></Card>}
export default function BillBookApp(){return <App/>;}
