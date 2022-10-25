
import logging
from copy import deepcopy
from telegram import Bot, ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError

from freqtrade.__init__ import __version__
from freqtrade.constants import DUST_PER_COIN
from freqtrade.enums import RPCMessageType
from freqtrade.exceptions import OperationalException
from freqtrade.misc import chunks, plural, round_coin_value
from freqtrade.persistence import Trade
from freqtrade.rpc import RPC, RPCException, RPCHandler

logger = logging.getLogger(__name__)
logger.debug('Included module rpc.telegram.channel ...')


class Telegram(RPCHandler):

    def __init__(self, rpc: RPC, config: dict) -> None:
        """
        Init the Telegram call, and init the super class RPCHandler
        :param rpc: instance of RPC Helper class
        :param config: Configuration object
        :return: None
        """
        super().__init__(rpc, config)

        self._bot: Bot
        self._init()

    def _init(self) -> None:
        self._bot = Bot(self._config['telegram']['token'])


    def _send_msg(self, msg: str, msg_type: RPCMessageType, parse_mode: str = ParseMode.HTML, disable_notification: bool = False) -> None:
        chat_ids = list()
        master_ids = self._config['channel'].get('master', [])
        if len(master_ids) and msg_type not in [RPCMessageType.STARTUP]:
            for master in master_ids:
                for name,id in master.items():
                    namespace = name.split('-')[0]
                    chat_ids.append(id)

        slave = self._config['telegram'].get('chat_id', '')
        if slave and msg_type not in [RPCMessageType.STARTUP, RPCMessageType.STATUS]:
            chat_ids.append(slave)

        for chat_id in chat_ids:
            try:
                try:
                    self._bot.send_message(
                        chat_id,
                        text=msg,
                        parse_mode=parse_mode,
                        disable_notification=disable_notification
                    )
                except NetworkError as err:
                    logger.warning(
                        'Telegram NetworkError: %s! Trying one more time.', err.message
                    )
                    self._bot.send_message(
                        chat_id,
                        text=msg,
                        parse_mode=parse_mode,
                        disable_notification=disable_notification
                    )
            except TelegramError as err:
                logger.warning(
                    'TelegramError: %s! Giving up on that message.', err.message
                )


    def send_msg(self, msg: dict) -> None:
        """ Send a message to telegram channel """
        default_noti = 'on'
        msg_type = msg['type']
        noti = ''
        if msg_type == RPCMessageType.EXIT:
            sell_noti = self._config['telegram'].get('notification_settings', {}).get(str(msg_type), {})
            if isinstance(sell_noti, str):
                noti = sell_noti
            else:
                noti = sell_noti.get(str(msg['exit_reason']), default_noti)
        else:
            noti = self._config['telegram'].get('notification_settings', {}).get(str(msg_type), default_noti)

        if noti == 'off':
            logger.info(f"Notification '{msg_type}' not sent.")
            return
        message = self.compose_message(deepcopy(msg), msg_type)
        if message:
            self._send_msg(message, msg_type, parse_mode=ParseMode.HTML, disable_notification=(noti=='silent'))


    def _format_entry_msg(self, msg: dict) -> str:
        msg['stake_amount_fiat'] = 0.0
        if self._rpc._fiat_converter:
            msg['stake_amount_fiat'] = self._rpc._fiat_converter.convert_amount(
                msg['stake_amount'], msg['stake_currency'], msg['fiat_currency']
            )

        is_fill = msg['type'] in [RPCMessageType.ENTRY_FILL]
        msg['emoji'] = '\N{SPARKLE}' if is_fill else '\N{LARGE BLUE DIAMOND}'
        entry_side = ({'enter': 'Long', 'entered': 'Longed'} if msg['direction'] == 'Long' else {'enter': 'Short', 'entered': 'Shorted'})

        message = [self._add_analyzed_candle(msg['pair'])]
        message += [f"{msg['emoji']} <b>{msg['exchange'].upper()}:::{msg['uid']}, #{msg['trade_id']}</b>"]
        message += [f"* <em>Order - ENTRY - {entry_side['entered'] if is_fill else entry_side['enter']}, {msg['pair']}</em>"]
        if msg.get('enter_tag'):
            message += [f"- ENTRY Tag: {msg['enter_tag']}"]
        message += [f"- Amount: {msg['amount']:.4f}"]

        if msg.get('leverage') and msg.get('leverage', 1.0) != 1.0:
            message += [f"- Leverage: {msg['leverage']}"]

        if msg['type'] == RPCMessageType.ENTRY_FILL:
            message += [f"- Rate, open: {msg['open_rate']:.4f}"]
        elif msg['type'] == RPCMessageType.ENTRY:
            message += [f"- Rate, open: {msg['open_rate']:.4f}"]
            message += [f"- Rate, current: {msg['current_rate']:.4f}"]

        total = f"- Total: {round_coin_value(msg['stake_amount'], msg['stake_currency'])}"
        if msg.get('fiat_currency'):
            total += f" ({round_coin_value(msg['stake_amount_fiat'], msg['fiat_currency'])})"
        message += [total]
        return '\n'.join(message)


    def _format_exit_msg(self, msg: dict) -> str:
        msg['amount'] = round(msg['amount'], 4)
        msg['profit_percent'] = round(msg['profit_ratio'] * 100, 2)
        msg['duration'] = msg['close_date'].replace(microsecond=0) - msg['open_date'].replace(microsecond=0)
        msg['duration_min'] = msg['duration'].total_seconds() / 60

        is_sub_trade = msg.get('sub_trade')
        is_fill = msg['type'] in [RPCMessageType.EXIT_FILL]
        is_sub_profit = msg['profit_amount'] != msg.get('cumulative_profit')
        profit_prefix = ('Sub ' if is_sub_profit else 'Cumulative ') if is_sub_trade else ''

        msg['enter_tag'] = msg['enter_tag'] if "enter_tag" in msg.keys() else None
        msg['emoji'] = '\N{LARGE RED CIRCLE}' if is_fill else self._get_sell_emoji(msg)
        msg['leverage_text'] = (f"- Leverage: {msg['leverage']:.1f}" if msg.get('leverage') and msg.get('leverage', 1.0) != 1.0 else "")

        msg['profit_extra'] = None
        if (all(prop in msg for prop in ['gain', 'fiat_currency', 'stake_currency']) and self._rpc._fiat_converter):
            msg['profit_fiat'] = self._rpc._fiat_converter.convert_amount(
                msg['profit_amount'],
                msg['stake_currency'],
                msg['fiat_currency']
            )
            msg['profit_extra'] = f"{msg['profit_amount']:.2f} {msg['stake_currency']} ({msg['profit_fiat']:.2f} {msg['fiat_currency']})"

        cp_extra = ''
        if is_sub_profit and is_sub_trade:
            cp_fiat = self._rpc._fiat_converter.convert_amount(msg['cumulative_profit'], msg['stake_currency'], msg['fiat_currency'])
            cp_extra = f" / {msg['fiat_currency']} {cp_fiat:.3f}"
            cp_extra = f"- Cumulative Profit: ({msg['cumulative_profit']:.4f} {msg['stake_currency']}{cp_extra})"

        message = [f"{msg['emoji']} <b>{msg['exchange']}:::{msg['uid']}, #{msg['trade_id']}</b>"]
        message += [f"* <em>Order - EXIT - {'exited' if is_fill else 'exiting'}, {msg['pair']}</em>"]
        message += [f"- {f'{profit_prefix}Profit, trade' if is_fill else f'{profit_prefix}Profit, unrealized'}: {cp_extra} {msg['profit_percent']}%"]

        if msg.get('profit_extra'):
            message += [f"- {msg['gain'].capitalize()}: {msg['profit_extra']}"]

        message += [f"- ENTRY Tag: {msg['enter_tag']}"]
        if msg.get('exit_tag'):
            message += [f"- EXIT Tag: {msg['exit_tag']}"]
        message += [f"- Reason: {msg['exit_reason'] and msg['exit_reason'].upper().replace('_', ' ')}"]
        message += [f"- Duration: {msg['duration']} ({msg['duration_min']:.1f}m)"]
        message += [f"- Amount: {msg['amount']:.4f}"]
        message += [f"- Direction: {msg['direction']}"]
        message += [f"- Rate, open: {msg['open_rate']:.4f}"]

        if msg['type'] == RPCMessageType.EXIT:
            message += [f"- Rate, current: {msg['current_rate']:.4f}"]
            if msg['order_rate']:
                message += [f"- Rate, exit: {msg['order_rate']:.4f}"]

        elif msg['type'] == RPCMessageType.EXIT_FILL:
            message += [f"- Rate, close: {msg['close_rate']:.4f}"]

        if msg.get('sub_trade'):
            if self._rpc._fiat_converter:
                msg['stake_amount_fiat'] = self._rpc._fiat_converter.convert_amount(msg['stake_amount'], msg['stake_currency'], msg['fiat_currency'])
            else:
                msg['stake_amount_fiat'] = 0
            message += [f"- Remaining: {round_coin_value(msg['stake_amount'], msg['stake_currency'])}"]

            if msg.get('fiat_currency', None):
                message += f", {round_coin_value(msg['stake_amount_fiat'], msg['fiat_currency'])}"
        return '\n'.join(message)


    def compose_message(self, msg: dict, msg_type: RPCMessageType) -> str:
        msg['uid'] = self._config.get('uid')
        msg['exchange'] = self._config.get('exchange').get('name').upper()

        message = None
        if msg_type in [RPCMessageType.ENTRY, RPCMessageType.ENTRY_FILL]:
            message = self._format_entry_msg(msg)

        elif msg_type in [RPCMessageType.EXIT, RPCMessageType.EXIT_FILL]:
            message = self._format_exit_msg(msg)

        elif msg_type in (RPCMessageType.ENTRY_CANCEL, RPCMessageType.EXIT_CANCEL):
            emoji = '\N{ANGER SYMBOL}'
            msg['message_side'] = 'enter' if msg['type'] == RPCMessageType.ENTRY_CANCEL else 'exit'
            message = [f"{emoji} <b>{msg['exchange']}:::{msg['uid']}, #{msg['trade_id']}</b>"]
            message += [f"<em>* Order - {msg['message_side']} - {msg['pair']}, {'cancelling partial' if msg.get('sub_trade') else 'canceled'}</em>"]
            message += [f"- Reason: {msg['reason']}"]
            message = '\n'.join(message)

        elif msg_type == RPCMessageType.PROTECTION_TRIGGER:
            emoji = '\N{CURLY LOOP}'
            message = f"{emoji} - Protection triggered due to {msg['reason']}. {msg['pair']} will be locked until {msg['lock_end_time']}"

        elif msg_type == RPCMessageType.PROTECTION_TRIGGER_GLOBAL:
            emoji = '\N{CURLY LOOP}'
            message = f"{emoji} - Protection triggered due to {msg['reason']}. ALL PAIRS will be locked until {msg['lock_end_time']}"

        elif msg_type == RPCMessageType.STATUS:
            emoji = '\N{GEAR}'
            message = [f"{emoji} - <b>{msg['exchange']}:::{msg['uid']}</b> -"]
            message += [f"- Status: {msg['status']}"]
            message = '\n'.join(message)

        elif msg_type == RPCMessageType.WARNING:
            emoji = '\N{WARNING SIGN}'
            message = f"{emoji} - <b>{msg['exchange']}:::{msg['uid']}</b> -\n- Warning: {msg['type']} / {msg['status']}"

        elif msg_type == RPCMessageType.STARTUP:
            emoji = '\N{GEAR}'
            message = f"{emoji} - <b>{msg['exchange']}:::{msg['uid']}</b> -\n- Type: {msg['type']}\n- <em>{msg['status']}</em> -"

        elif msg_type == RPCMessageType.STRATEGY_MSG:
            message = f"{msg['exchange']}:::{msg['msg']}"

        else:
            message = f"{msg['exchange']}:::{msg['msg']}"
            logger.debug("Unknown message type: %s", msg_type)
            # raise Warning(f"{msg.get('exchange', None)}:::{msg.get('uid', None)} Unknown message type: {msg_type}")

        # logger.info(f"Proccessing to message type {msg_type} | body: {msg}")
        return message


    def _get_sell_emoji(self, msg: dict) -> str:
        if float(msg['profit_percent']) >= 5.0:
            return "\N{ROCKET}"
        elif float(msg['profit_percent']) >= 0.0:
            return "\N{CURRENCY EXCHANGE}"
        elif msg['sell_reason'] == "stoploss":
            return "\N{WARNING SIGN}"
        else:
            return "\N{CROSS MARK}"

    def _add_analyzed_candle(self, pair: str) -> str:
        candle_val = self._config['telegram'].get('notification_settings', {}).get('show_candle', 'off')
        if candle_val != 'off':
            if candle_val == 'ohlc':
                analyzed_df, _ = self._rpc._freqtrade.dataprovider.get_analyzed_dataframe(
                    pair, self._config['timeframe'])
                candle = analyzed_df.iloc[-1].squeeze() if len(analyzed_df) > 0 else None
                if candle is not None:
                    return (
                        f"- Candle OHLC: {candle['open']}, {candle['high']}, {candle['low']}, {candle['close']}"
                    )
        return ''
