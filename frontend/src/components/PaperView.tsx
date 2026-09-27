import { useState } from "react";

import { api } from "../api";
import type { Portfolio } from "../types";
import { label, money, price, qty, shortId, time } from "../lib/format";
import { useResource } from "../lib/useResource";
import { Badge, Card, Empty, ErrorNote, Loading, Td, Th, statusTone } from "./ui";

export function PaperView({ version, portfolio }: { version: number; portfolio: Portfolio | undefined }) {
  const executions = useResource(() => api.executions(), [version]);
  const orders = useResource(() => api.orders(), [version]);
  return (
    <div className="grid gap-4">
      <Card title="Paper executions">
        {executions.error && <ErrorNote message={executions.error} />}
        {!executions.data ? (
          <Loading />
        ) : executions.data.length === 0 ? (
          <Empty>No paper executions yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px]" data-testid="execution-table">
              <thead>
                <tr>
                  <Th>outcome</Th>
                  <Th>scenario · policy</Th>
                  <Th right>hedged / requested</Th>
                  <Th right>residual</Th>
                  <Th right>fees</Th>
                  <Th right>locked-in</Th>
                  <Th right>realized</Th>
                  <Th>reason</Th>
                </tr>
              </thead>
              <tbody>
                {executions.data.map((execution) => (
                  <tr key={execution.id} className="border-t border-slate-100">
                    <Td>
                      <Badge tone={statusTone(execution.outcome)}>{label(execution.outcome)}</Badge>
                    </Td>
                    <Td className="text-xs">
                      {label(execution.scenario)} · {label(execution.policy)}
                      <div className="font-mono text-[10px] text-slate-400">{execution.id}</div>
                    </Td>
                    <Td right>
                      {qty(execution.hedged_quantity)} / {qty(execution.requested_quantity)}
                    </Td>
                    <Td right className={Number(execution.residual_quantity) > 0 ? "text-rose-700" : ""}>
                      {qty(execution.residual_quantity)}
                    </Td>
                    <Td right>{money(execution.total_fees, 4)}</Td>
                    <Td right>{money(execution.expected_locked_in_pnl, 4)}</Td>
                    <Td right>{money(execution.realized_pnl, 4)}</Td>
                    <Td className="max-w-xs text-xs text-slate-600">{execution.reason}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Paper orders and fills (simulated IOC)">
        {orders.error && <ErrorNote message={orders.error} />}
        {!orders.data ? (
          <Loading />
        ) : orders.data.length === 0 ? (
          <Empty>No simulated orders yet.</Empty>
        ) : (
          <OrdersTable orders={orders.data} />
        )}
      </Card>
      <Card title="Positions and residual exposure">
        {!portfolio || portfolio.positions.length === 0 ? (
          <Empty>No paper positions.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]" data-testid="positions-table">
              <thead>
                <tr>
                  <Th>venue · outcome</Th>
                  <Th right>qty</Th>
                  <Th right>avg cost</Th>
                  <Th right>mark (bid)</Th>
                  <Th right>unrealized</Th>
                  <Th right>realized</Th>
                  <Th>updated</Th>
                </tr>
              </thead>
              <tbody>
                {portfolio.positions.map((position) => (
                  <tr key={position.key} className="border-t border-slate-100">
                    <Td>
                      <span className="capitalize">{position.venue}</span> {position.outcome_side.toUpperCase()}
                      <div className="font-mono text-[10px] text-slate-400">{shortId(position.market_id, 28)}</div>
                    </Td>
                    <Td right>{qty(position.quantity)}</Td>
                    <Td right>{price(position.average_cost)}</Td>
                    <Td right>{price(position.mark_price)}</Td>
                    <Td right>{money(position.unrealized_pnl, 4)}</Td>
                    <Td right>{money(position.realized_pnl, 4)}</Td>
                    <Td className="text-xs">{time(position.updated_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-slate-500">
              Residual exposure (positions not covered by a verified hedged bundle): {money(portfolio.residual_exposure_notional)}.
              Marks use the best bid, a conservative liquidation value; hedged bundles are valued by their locked-in settlement payout.
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}

function OrdersTable({ orders }: { orders: NonNullable<ReturnType<typeof useResource<Awaited<ReturnType<typeof api.orders>>>>["data"]> }) {
  const [open, setOpen] = useState<string>();
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px]" data-testid="orders-table">
        <thead>
          <tr>
            <Th>status</Th>
            <Th>order</Th>
            <Th right>requested</Th>
            <Th right>filled</Th>
            <Th right>avg price</Th>
            <Th right>limit</Th>
            <Th right>fees</Th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr
              key={order.id}
              className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
              onClick={() => setOpen(open === order.id ? undefined : order.id)}
            >
              <Td>
                <Badge tone={statusTone(order.status)}>{label(order.status)}</Badge>
                {order.simulated && <div className="mt-0.5 text-[10px] text-slate-400">simulated</div>}
              </Td>
              <Td>
                <div className="text-sm">
                  {order.action.toUpperCase()} {order.outcome_side.toUpperCase()} on <span className="capitalize">{order.venue}</span> ({order.purpose})
                </div>
                {order.reject_reason && <div className="text-xs text-slate-500">{order.reject_reason}</div>}
                {open === order.id && (
                  <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
                    {order.fills.map((fill) => (
                      <li key={fill.id}>
                        fill {qty(fill.quantity)} @ {price(fill.price)} · fee {money(fill.fee, 5)}
                      </li>
                    ))}
                    {order.fills.length === 0 && <li>no fills</li>}
                  </ul>
                )}
              </Td>
              <Td right>{qty(order.requested_quantity)}</Td>
              <Td right>{qty(order.filled_quantity)}</Td>
              <Td right>{price(order.average_price)}</Td>
              <Td right>{price(order.limit_price)}</Td>
              <Td right>{money(order.fees, 4)}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
