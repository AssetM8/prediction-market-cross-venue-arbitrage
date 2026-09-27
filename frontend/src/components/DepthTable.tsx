import type { ArbitrageLeg, OrderBook } from "../types";
import { num, price, qty } from "../lib/format";
import { Badge } from "./ui";

/** Ask/bid ladder for one book; ask levels consumed by the opportunity are highlighted. */
export function DepthTable({ book, leg }: { book: OrderBook; leg?: ArbitrageLeg }) {
  const consumed = new Map((leg?.fills ?? []).map((fill) => [num(fill.price), num(fill.quantity)]));
  const asks = book.asks.slice(0, 8);
  const bids = book.bids.slice(0, 5);
  return (
    <div className="min-w-0 rounded-lg border border-slate-200">
      <div className="flex flex-wrap items-center justify-between gap-1 border-b border-slate-100 px-2 py-1.5 text-xs">
        <span className="font-semibold capitalize">
          {book.venue} · {book.outcome_side.toUpperCase()}
        </span>
        <span className="flex gap-1">
          {book.derived_asks && <Badge tone="info" title="Kalshi publishes bids only; asks = 1 - opposite bids">derived asks</Badge>}
          {book.depth_limited && <Badge tone="warn">top of book only</Badge>}
          {book.integrity_issues.length > 0 && <Badge tone="bad">{book.integrity_issues.length} issue(s)</Badge>}
        </span>
      </div>
      <table className="w-full text-xs tabular-nums">
        <thead>
          <tr className="text-slate-500">
            <th className="px-2 py-1 text-left font-medium">side</th>
            <th className="px-2 py-1 text-right font-medium">price</th>
            <th className="px-2 py-1 text-right font-medium">size</th>
            <th className="px-2 py-1 text-right font-medium">used</th>
          </tr>
        </thead>
        <tbody>
          {[...asks].reverse().map((level) => {
            const used = consumed.get(num(level.price));
            return (
              <tr key={`a-${level.price}`} className={used ? "bg-amber-50" : ""}>
                <td className="px-2 py-0.5 text-rose-700">ask</td>
                <td className="px-2 py-0.5 text-right">{price(level.price)}</td>
                <td className="px-2 py-0.5 text-right">{qty(level.quantity)}</td>
                <td className="px-2 py-0.5 text-right font-semibold text-amber-800">{used ? qty(String(used)) : ""}</td>
              </tr>
            );
          })}
          {bids.map((level) => (
            <tr key={`b-${level.price}`}>
              <td className="px-2 py-0.5 text-emerald-700">bid</td>
              <td className="px-2 py-0.5 text-right">{price(level.price)}</td>
              <td className="px-2 py-0.5 text-right">{qty(level.quantity)}</td>
              <td />
            </tr>
          ))}
          {asks.length === 0 && bids.length === 0 && (
            <tr>
              <td colSpan={4} className="px-2 py-2 text-center text-slate-400">
                empty book
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <div className="truncate border-t border-slate-100 px-2 py-1 font-mono text-[10px] text-slate-400" title={book.checksum_or_source_hash}>
        hash {book.checksum_or_source_hash}
      </div>
    </div>
  );
}
