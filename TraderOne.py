import asyncio
import itertools
from typing import List
from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

# Constants
LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS


class AutoTrader(BaseAutoTrader):
    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):

        """
        Initializes a new instance of the AutoTrader class.

        Args:
            loop: The asyncio event loop to use for asynchronous operations.
            team_name: The name of your trading team.
            secret: Your team's secret API key.
        """

        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:

        """
        Called when the exchange detects an error with an order.

        Args:
            client_order_id: The ID of the order that encountered the error.
            error_message: A byte string containing the error message from the exchange.
        """

        self.logger.warning("Error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:

        """
        This function might be specific to the 'ready_trader_go' library and might not be used in real trading.

        Args:
            client_order_id: (Description from original code)
            price: (Description from original code)
            volume: (Description from original code)
        """

        self.logger.info("Received hedge filled for order %d with average price %d and volume %d",
                         client_order_id, price, volume)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:

        """
        Called periodically to report the latest order book update for a specific instrument.

        Args:
            instrument: The instrument (e.g., futures contract) for which the order book update applies.
            sequence_number: A sequence number to identify the order of updates (may not be relevant for all exchanges).
            ask_prices: A list of current ask (sell) prices at different quantities.
            ask_volumes: A list of volumes available at each corresponding ask price.
            bid_prices: A list of current bid (buy) prices at different quantities.
            bid_volumes: A list of volumes available at each corresponding bid price.
        """

        self.logger.info("Received order book for instrument %d with sequence number %d", instrument, sequence_number)

        if instrument == Instrument.FUTURE:
            # Calculate mid price
            mid_price = (bid_prices[0] + ask_prices[0]) // 2

            # Determine new bid and ask prices based on position
            if self.position > 0:
                new_ask_price = mid_price
                new_bid_price = mid_price - 10
            elif self.position < 0:
                new_ask_price = mid_price + 10
                new_bid_price = mid_price
            else:
                new_bid_price = bid_prices[0]
                new_ask_price = ask_prices[0]

            # Cancel existing orders if necessary
            if self.bid_id != 0 and new_bid_price not in (self.bid_price, 0):
                self.send_cancel_order(self.bid_id)
                self.bid_id = 0
            if self.ask_id != 0 and new_ask_price not in (self.ask_price, 0):
                self.send_cancel_order(self.ask_id)
                self.ask_id = 0

            # Place new orders if conditions are met
            if self.bid_id == 0 and new_bid_price != 0 and self.position + LOT_SIZE < POSITION_LIMIT:
                self.bid_id = next(self.order_ids)
                self.bid_price = new_bid_price
                self.send_insert_order(self.bid_id, Side.BUY, new_bid_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                self.bids.add(self.bid_id)
            elif self.bid_id != 0 and new_bid_price == 0:
                # Cancel the existing bid order if the new bid price is 0
                self.send_cancel_order(self.bid_id)
                self.bid_id = 0

            if self.ask_id == 0 and new_ask_price != 0 and self.position - LOT_SIZE > -POSITION_LIMIT:
                self.ask_id = next(self.order_ids)
                self.ask_price = new_ask_price
                self.send_insert_order(self.ask_id, Side.SELL, new_ask_price, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                self.asks.add(self.ask_id)
            elif self.ask_id != 0 and new_ask_price == 0:
                # Cancel the existing ask order if the new ask price is 0
                self.send_cancel_order(self.ask_id)
                self.ask_id = 0

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully."""

        """
        Called when one of your orders is filled, partially or fully.

            Args:
                client_order_id: The ID of the order that was filled.
                price: The average price at which the order was filled.
                volume: The total volume filled for the order.
        """
        self.logger.info("Received order filled for order %d with price %d and volume %d",
                         client_order_id, price, volume)
        if client_order_id in self.bids:
            self.position += volume
        elif client_order_id in self.asks:
            self.position -= volume

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:

        """
        Called when the status of one of your orders changes (e.g., filled, canceled, rejected).

        Args:
            client_order_id: The ID of the order whose status changed.
            fill_volume: The total volume filled for the order so far.
            remaining_volume: The remaining volume of the order that hasn't been filled yet.
            fees: The fees charged by the exchange for this order (if any).
        """
        self.logger.info("Received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # Remove order from bids or asks
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:

        """
        This function might be exchange specific and might not be relevant for all trading strategies.
        It's called periodically when there's a trade on the instrument (e.g., a price change).

        Args:
            instrument: (Description from original code)
            sequence_number: (Description from original code)
            ask_prices: (Description from original code)
            ask_volumes: (Description from original code)
            bid_prices: (Description from original code)
            bid_volumes: (Description from original code)
        """

        self.logger.info("Received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)
