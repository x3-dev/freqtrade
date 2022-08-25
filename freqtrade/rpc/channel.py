
import logging
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
        self._bot = Bot(self._config['channel']['token'])


    def send_msg(self, msg: dict) -> None:
        """ Send a message to telegram channel """

        default_noti = 'on'
        msg_type = msg['type']

        noti = ''
        if msg_type == RPCMessageType.SELL:
            sell_noti = self._config['channel'].get('notification_settings', {}).get(str(msg_type), {})
            # For backward compatibility sell still can be string
            if isinstance(sell_noti, str):
                noti = sell_noti
            else:
                noti = sell_noti.get(str(msg['sell_reason']), default_noti)
        else:
            noti = self._config['channel'].get('notification_settings', {}).get(str(msg_type), default_noti)

        if noti == 'off':
            logger.info(f"Notification '{msg_type}' not sent.")
            # Notification disabled
            return

        message = self.compose_message(msg, msg_type)
        self._send_msg(message, msg_type, parse_mode=ParseMode.HTML, disable_notification=(noti == 'silent'))


    def _send_msg(self, msg: str, msg_type: RPCMessageType, parse_mode: str = ParseMode.HTML, disable_notification: bool = False) -> None:
        chat_ids = []
        master_ids = self._config['channel'].get('master', [])
        slave = self._config['channel'].get('chat_id', '')
        if len(master_ids) and msg_type not in [RPCMessageType.STARTUP]:
            for master in master_ids:
                for name,id in master.items():
                    namespace = name.split('-')[0]
                    chat_ids.append(id)

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


    def _format_buy_msg(self, msg: dict) -> str:
        msg['stake_amount_fiat'] = 0.0
        if self._rpc._fiat_converter:
            msg['stake_amount_fiat'] = self._rpc._fiat_converter.convert_amount(
                msg['stake_amount'], msg['stake_currency'], msg['fiat_currency']
            )

        is_fill = bool(msg['type'] == RPCMessageType.BUY_FILL)
        msg['emoji'] = '\N{SPARKLE}' if is_fill else '\N{LARGE BLUE DIAMOND}'

        message = [f"{msg['emoji']} <b>{msg['exchange'].upper()}:::{msg['uid']}, #{msg['trade_id']}</b>"]
        message += [f"* <em>Order - BUY - {'filled' if is_fill else 'created'}, {msg['pair']}</em>"]
        if msg.get('buy_tag', None):
            message += [f"- BUY Tag: {msg['buy_tag']}"]
        message += [f"- Amount: {msg['amount']:.4f}"]

        if msg['type'] == RPCMessageType.BUY_FILL:
            message += [f"- Rate, open: {msg['open_rate']:.4f}"]
        elif msg['type'] == RPCMessageType.BUY:
            message += [f"- Rate, open: {msg['limit']:.4f}"]
            message += [f"- Rate, current: {msg['current_rate']:.4f}"]

        total = f"- Total: {round_coin_value(msg['stake_amount'], msg['stake_currency'])}"
        if msg.get('fiat_currency', None):
            total += f" ({round_coin_value(msg['stake_amount_fiat'], msg['fiat_currency'])})"
        message += [total]
        return '\n'.join(message)


    def _format_sell_msg(self, msg: dict) -> str:
        msg['amount'] = round(msg['amount'], 4)
        msg['profit_percent'] = round(msg['profit_ratio'] * 100, 2)
        msg['duration'] = msg['close_date'].replace(microsecond=0) - msg['open_date'].replace(microsecond=0)
        msg['duration_min'] = msg['duration'].total_seconds() / 60

        is_fill = bool(msg['type'] == RPCMessageType.SELL_FILL)
        msg['buy_tag'] = msg['buy_tag'] if 'buy_tag' in msg.keys() else None
        msg['emoji'] = '\N{LARGE RED CIRCLE}' if is_fill else self._get_sell_emoji(msg)

        msg['profit_extra'] = None
        if (all(prop in msg for prop in ['gain', 'fiat_currency', 'stake_currency']) and self._rpc._fiat_converter):
            msg['profit_fiat'] = self._rpc._fiat_converter.convert_amount(
                msg['profit_amount'],
                msg['stake_currency'],
                msg['fiat_currency']
            )
            msg['profit_extra'] = f"{msg['profit_amount']:.2f} {msg['stake_currency']} ({msg['profit_fiat']:.2f} {msg['fiat_currency']})"

        message = [f"{msg['emoji']} <b>{msg['exchange']}:::{msg['uid']}, #{msg['trade_id']}</b>"]
        message += [f"* <em>Order - SELL - {'filled' if is_fill else 'created'}, {msg['pair']}</em>"]

        message += [f"- {'Profit, trade' if is_fill else 'Profit, take'}: {msg['profit_percent']}%"]
        if msg.get('profit_extra', None):
            message += [f"- {msg['gain'].capitalize()}: {msg['profit_extra']}"]

        message += [f"- BUY Tag: {msg['buy_tag']}"]
        if msg.get('exit_tag', None):
            message += [f"- SELL Tag: {msg['exit_tag']}"]
        message += [f"- Reason: {msg['sell_reason'] and msg['sell_reason'].upper().replace('_', ' ')}"]
        message += [f"- Duration: {msg['duration']} ({msg['duration_min']:.1f}m)"]
        message += [f"- Amount: {msg['amount']:.4f}"]
        message += [f"- Rate, open: {msg['open_rate']:.4f}"]

        if msg['type'] == RPCMessageType.SELL:
            message += [f"- Rate, current: {msg['current_rate']:.4f}"]
            message += [f"- Rate, close: {msg['limit']:.4f}"]

        elif msg['type'] == RPCMessageType.SELL_FILL:
            message += [f"- Rate, close: {msg['limit']:.4f}"]
        return '\n'.join(message)


    def compose_message(self, msg: dict, msg_type: RPCMessageType) -> str:
        message = 'NONE'
        msg['uid'] = self._config.get('uid')
        msg['exchange'] = self._config.get('exchange').get('name').upper()

        if msg_type in [RPCMessageType.BUY, RPCMessageType.BUY_FILL]:
            message = self._format_buy_msg(msg)

        elif msg_type in [RPCMessageType.SELL, RPCMessageType.SELL_FILL]:
            message = self._format_sell_msg(msg)

        elif msg_type in (RPCMessageType.BUY_CANCEL, RPCMessageType.SELL_CANCEL):
            emoji = '\N{ANGER SYMBOL}'
            msg['side'] = 'BUY' if msg['type'] == RPCMessageType.BUY_CANCEL else 'SELL'
            message = [f"{emoji} <b>{msg['exchange']}:::{msg['uid']}, #{msg['trade_id']}</b>"]
            message += [f"<em>* Order - {msg['side']} - {msg['pair']}, canceled</em>"]
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

        else:
            raise Warning(f"{msg.get('exchange', None)}:::{msg.get('uid', None)} Unknown message type: {msg_type}")

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
