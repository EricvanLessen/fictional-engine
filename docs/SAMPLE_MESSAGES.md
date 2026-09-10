# Representative Telegram fixtures

These are sanitized representative inputs derived from supplied channel messages. Keep fixture IDs stable so replay tests remain reproducible.

## ignore-promotion-001

Classification: `PROMOTION`

```text
$54,181.90 paid out to five traders in one week.
The profit target has been reduced and accounts receive a discount.
Use discount code SEPT30.
```

Expected events: none.

## session-no-trading-001

Classification: `SESSION_CONTROL`

```text
Today we have bank holiday in USA.
We will not trade today. See you back tomorrow.
```

Expected event: `NoTradingDay`.

## advisory-summer-001

Classification: `RISK_ADVISORY`

```text
Between April 16 and September 30 we normally pause this strategy.
We will continue sending trades, but you should reduce your risk or take a break.
```

Expected broker effects: none. The message may be surfaced to the operator, but it must not mutate configured risk automatically.

## orders-initial-001

Classification: `TRADE_INSTRUCTION`

```text
Hello traders,

These are my first orders for today:

Instrument: US30

Cronos Markets data:
SELL STOP
Entry: 52547.00
SL: 52832.00 (-2 850.0 pips)
TP: 52413.00 (1 340.0 pips)

BUY STOP
Entry: 52829.00
SL: 52544.00 (-2 850.0 pips)
TP: 52963.00 (1 340.0 pips)

Funding Dynasty data:
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00
```

Expected: two ordered `PlacePendingOrder` events. Both provider blocks agree.

## orders-trigger-replace-001

Classification: `TRADE_INSTRUCTION` plus `ORDER_STATUS`

```text
The sell stop order was triggered.
Delete the buy stop order.

I've placed a new buy stop:

CronosMarkets data:
BUY STOP Order
Entry: 52832.00
SL: 52547.00 (-2 850.0 pips)
TP: 52893.00 (610.0 pips)

Funding Dynasty data:
BUY STOP Order
Entry: 52832.00
SL: 52547.00
TP: 52893.00
```

Expected ordered events:

1. `MarkOrderTriggered(SELL, STOP)`
2. `CancelPendingOrder(BUY, STOP)`
3. `PlacePendingOrder(BUY, STOP, 52832, 52547, 52893)`

## result-tp-001

Classification: `ORDER_STATUS`

```text
TP. 🥇
```

Expected: resolve against current session state. Exactly one eligible position yields `RecordTradeResult(TP)`; otherwise manual review.

## session-cancel-end-001

Classification: `TRADE_INSTRUCTION` plus `SESSION_CONTROL`

```text
Delete the buy stop order, we are ending today's session here.
```

Expected ordered events:

1. `CancelPendingOrder(BUY, STOP)`
2. `EndSession`

It must not close a filled Buy position and must not cancel an unspecified Sell order.

## Variants to add when observed

Capture real examples for:

- explicit close-now instruction
- move SL to break-even
- modify TP
- partial close
- multiple TP levels
- market order
- sell-stop replacement
- cancel all pending orders
- edited Telegram post
- signal delivered as caption or image
- correction after an erroneous signal

Do not implement a variant until its semantics and expected broker behavior are documented.
