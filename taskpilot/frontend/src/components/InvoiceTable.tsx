import type { Invoice } from "../types";
import { Empty, Section } from "./Common";

export default function InvoiceTable({ invoices, highlightTaskId }: { invoices: Invoice[]; highlightTaskId?: string }) {
  return (
    <Section title="Internal invoice system" aside={<span className="text-xs text-slate2">{invoices.length} record(s) in SQLite</span>}>
      {invoices.length === 0 ? (
        <Empty>No invoices saved yet. Approved records show up here straight from the database.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-slate2">
              <tr><th className="py-1 pr-3 font-medium">Number</th><th className="py-1 pr-3 font-medium">Company</th><th className="py-1 pr-3 font-medium">Due</th><th className="py-1 text-right font-medium">Amount</th></tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {invoices.map((i) => (
                <tr key={i.id} className={i.created_by_task_id === highlightTaskId ? "bg-signal/5" : ""}>
                  <td className="py-1.5 pr-3 font-medium">{i.invoice_number}</td>
                  <td className="py-1.5 pr-3">{i.company}</td>
                  <td className="py-1.5 pr-3 whitespace-nowrap">{i.due_date}</td>
                  <td className="py-1.5 text-right whitespace-nowrap">{i.currency} {i.amount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
