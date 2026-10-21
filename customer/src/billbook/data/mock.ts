import type {User,UserData,Customer,Product,Invoice,Payment,Expense,Supplier,ProductBatch} from '../types';
const clone=<T,>(x:T):T=>JSON.parse(JSON.stringify(x));
const customerNames=['ABC Traders','Sri Lakshmi Stores','Green Mart','Vijaya Agencies','Nandi Foods'];
const supplierNames=['AP Wholesale','Andhra Office Supplies','Tech Distributors'];
const productNames=['Laptop Pro 15','Wireless Mouse','Keyboard','USB-C Cable','Monitor 24','Office Chair','Printer','A4 Paper Pack','Desk Lamp','Barcode Scanner'];
const customers:Customer[]=customerNames.map((name,i)=>({id:`C${i+1}`,name,phone:`98765432${10+i}`,gstin:i%2===0?'37ABCDE1234F1Z5':undefined,email:`customer${i+1}@example.com`,address:'Main Road, Andhra Pradesh',sales:50000+i*18000,received:32000+i*9000,status:i%2?'Paid':'Receivable',openingBalance:i*5000}));
const suppliers:Supplier[]=supplierNames.map((name,i)=>({id:`S${i+1}`,name,phone:`99887766${10+i}`,gstin:'37SUPPLIER1234F1Z5',purchases:70000+i*25000,paid:50000+i*15000,openingBalance:i*3000}));
const products:Product[]=productNames.map((name,i)=>({id:`P${i+1}`,name,sku:`SKU-${String(i+1).padStart(3,'0')}`,barcode:`89010000000${i+1}`,category:i<5?'Electronics':'Office',hsnSac:i<5?'8471':'4820',purchasePrice:[39000,500,850,300,9800,5400,16000,330,1100,6000][i],price:[45000,750,1200,450,12000,6500,18500,420,1500,7200][i],tax:i%3===0?18:12,unit:'pcs',openingStock:20+i*3,stock:[12,8,3,25,42,18,7,55,5,9][i],minimumStock:3,reorder:8}));
const invoices:Invoice[]=Array.from({length:10},(_,i)=>{const c=customers[i%customers.length],p=products[i%products.length],q=i%3+1,total=p.price*q*(1+p.tax/100);return{id:`INV-${String(i+1).padStart(3,'0')}`,customerId:c.id,customer:c.name,date:'21 Sep 2026',dueDate:'30 Sep 2026',items:[{productId:p.id,name:p.name,qty:q,price:p.price,discount:0,tax:p.tax}],status:i%3===0?'Paid':'Pending',payment:i%3===0?'Paid':'Pending',total:Math.round(total)}})
const payments:Payment[]=invoices.slice(0,6).map((i,n)=>({id:`PAY-${n+1}`,party:i.customer,type:'Receipt',amount:i.status==='Paid'?i.total:2500,mode:['Cash','UPI','Card'][n%3],reference:i.id,date:'21 Sep 2026',status:'Completed',invoice:i.id}));
const expenses:Expense[]=[['Rent',25000],['Electricity',8500],['Transport',4200],['Office Supplies',3500],['Internet',1500]].map((x,i)=>({id:`EXP-${i+1}`,category:String(x[0]),amount:Number(x[1]),date:'21 Sep 2026',mode:['Bank','UPI','Cash'][i%3],vendor:'Demo Vendor',description:'Demo business expense'}));
const batches:ProductBatch[]=products.slice(0,6).map((p,i)=>({id:`B${i+1}`,productId:p.id,batchNo:`BAT-2026-${i+1}`,manufacturingDate:'01 Jun 2026',expiryDate:i===0?'25 Sep 2026':'30 Dec 2026',purchaseRate:p.purchasePrice,saleRate:p.price,quantity:p.stock,status:i===0?'Expiring Soon':'Active'}));
export function createDemoData(u:User):UserData{
 const cs=clone(customers).map((c,i)=>({...c,id:`${u.id}-C${i+1}`,name:i===0?`${u.name} Main Customer`:c.name}));
 const ps=clone(products).map((p,i)=>({...p,id:`${u.id}-P${i+1}`,name:i===0?`${u.name} Product`:p.name,barcode:`890${u.mobile.slice(-7)}${i}`.slice(0,13)}));
 const ss=clone(suppliers).map((s,i)=>({...s,id:`${u.id}-S${i+1}`}));
 const inv=clone(invoices).map((x,i)=>({...x,id:`${u.id}-INV-${i+1}`,customerId:cs[i%cs.length].id,customer:cs[i%cs.length].name}));
 return {
  customers:cs,suppliers:ss,products:ps,invoices:inv,payments:clone(payments),expenses:clone(expenses),
  purchases:suppliers.map((s,i)=>({id:`PUR-${i+1}`,supplierId:ss[i].id,supplier:ss[i].name,date:'21 Sep 2026',total:s.purchases,status:'Paid'})),
  quotations:customerNames.slice(0,3).map((n,i)=>({id:`QUO-${i+1}`,customerId:cs[i].id,customer:cs[i].name,date:'20 Sep 2026',total:15000+i*5000,status:'Draft'})),
  salesOrders:customerNames.slice(0,3).map((n,i)=>({id:`SO-${i+1}`,customerId:cs[i].id,customer:cs[i].name,date:'20 Sep 2026',total:18000+i*4000,status:'Confirmed'})),
  returns:[],ledgers:[],generalLedger:[],journalEntries:[],salesLedger:[],
  reminders:inv.filter(i=>i.payment!=='Paid').map((i,n)=>({id:`REM-${n+1}`,customer:i.customer,invoice:i.id,dueDate:i.dueDate,amount:i.total,status:'Pending'})),
  batches:clone(batches).map(b=>({...b,productId:ps.find(p=>p.name===products.find(x=>x.id===b.productId)?.name)?.id||ps[0].id})),
  barcodeData:[],heldBills:[],posBills:[],auditLogs:[{id:'audit-seed',date:'21 Sep 2026',time:'09:00 AM',user:u.name,action:'Demo data created',module:'System',reference:u.id}],
  notifications:[{id:'n1',text:'3 products are low in stock',read:false},{id:'n2',text:'Invoice payment is due',read:false}],
  otherIncome:[{id:'INC-1',category:'Commission',amount:5000,date:'20 Sep 2026',mode:'Bank',description:'Demo commission'}],
  creditNotes:[],debitNotes:[],calendarEvents:[{id:'ev1',title:'Payment Due - ABC Traders',date:'2026-09-21',time:'10:00',description:'Follow-up',type:'Payment Due'}],
  businessProfile:{businessName:u.name,ownerName:u.name,mobile:u.mobile,email:'demo@billbooks.example',address:'Main Road, Andhra Pradesh, India',gstin:'37ABCDE1234F1Z5',businessType:'Retail',financialYear:'2026-27'},
  invoiceSettings:{businessName:u.name,gstin:'37ABCDE1234F1Z5',phone:u.mobile,email:'demo@billbooks.example',address:'Main Road, Andhra Pradesh, India',bankDetails:'Demo Bank • A/C XXXX1234',upiId:'billbooks@upi',upiQr:'',signature:'',terms:'Payment due as stated on invoice.',notes:'Thank you for your business.',theme:'Modern',prefix:'INV-',decimals:2,taxDisplay:true,discountDisplay:true,shippingAddress:false,paper:'A4'},
  permissions:{role:u.role,permissions:{View:true,Create:true,Edit:true,Delete:true,Print:true,Export:true}},bankBalance:125400,cashBalance:34280,
  recurringInvoices:[{id:'RI-1',customer:cs[0].name,frequency:'Monthly',amount:12000,nextDate:'01 Oct 2026',status:'Active'}],
  expenseClaims:[{id:'CL-1',employee:u.name,amount:1200,category:'Travel',status:'Pending',date:'20 Sep 2026'}],
  categories:['Electronics','Office','Services'],warehouses:['Main Warehouse']
 };
}
export const demoUsers=[['9876543210','ABC Traders'],['9123456780','Sri Lakshmi Stores'],['9988776655','BillBooks Retail']];
