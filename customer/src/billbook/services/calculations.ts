import type {UserData,GeneralLedgerEntry,JournalEntry} from '../types';
export const money=(n:number)=>`₹${Math.round(n||0).toLocaleString('en-IN')}`;
export const today=()=>new Date().toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});
export const totals=(d:UserData)=>{
 const sales=d.invoices.reduce((a,i)=>a+i.total,0);
 const purchases=d.purchases.reduce((a,p)=>a+p.total,0);
 const expenses=d.expenses.reduce((a,e)=>a+e.amount,0);
 const income=d.otherIncome.reduce((a,e)=>a+e.amount,0);
 const received=d.payments.filter(p=>p.type==='Receipt').reduce((a,p)=>a+p.amount,0);
 const paid=d.payments.filter(p=>p.type!=='Receipt').reduce((a,p)=>a+p.amount,0);
 return {sales,purchases,expenses,income,received,paid,receivables:d.customers.reduce((a,c)=>a+Math.max(0,c.sales-c.received),0),payables:d.suppliers.reduce((a,s)=>a+Math.max(0,s.purchases-s.paid),0),profit:sales-purchases-expenses+income,cash:d.cashBalance,bank:d.bankBalance,lowStock:d.products.filter(p=>p.stock<=p.reorder).length,expiring:d.batches.filter(b=>b.status==='Expiring Soon').length};
};
export function buildGeneralLedger(d:UserData):GeneralLedgerEntry[]{
 const out:GeneralLedgerEntry[]=[];
 d.invoices.forEach(i=>{out.push({id:`GL-S-${i.id}`,date:i.date,account:'Sales',particular:`Invoice ${i.id} • ${i.customer}`,debit:0,credit:i.total,reference:i.id});out.push({id:`GL-R-${i.id}`,date:i.date,account:'Accounts Receivable',particular:`Receivable ${i.id}`,debit:i.total,credit:0,reference:i.id})});
 d.payments.forEach(p=>{out.push({id:`GL-P-${p.id}`,date:p.date,account:p.type==='Receipt'?'Cash/Bank':'Expense/Payable',particular:`${p.type} • ${p.party}`,debit:p.type==='Receipt'?0:p.amount,credit:p.type==='Receipt'?p.amount:0,reference:p.reference})});
 d.purchases.forEach(p=>out.push({id:`GL-B-${p.id}`,date:p.date,account:'Purchases',particular:`Purchase ${p.id} • ${p.supplier}`,debit:p.total,credit:0,reference:p.id}));
 d.expenses.forEach(e=>out.push({id:`GL-E-${e.id}`,date:e.date,account:'Expenses',particular:`${e.category} • ${e.vendor}`,debit:e.amount,credit:0,reference:e.id}));
 d.otherIncome.forEach(i=>out.push({id:`GL-I-${i.id}`,date:i.date,account:'Other Income',particular:i.category,debit:0,credit:i.amount,reference:i.id}));
 return out;
}
export function buildJournals(d:UserData):JournalEntry[]{return buildGeneralLedger(d).map(x=>({id:x.id,date:x.date,reference:x.reference||x.id,narration:x.particular,lines:[{account:x.account,debit:x.debit,credit:x.credit}]}))}
