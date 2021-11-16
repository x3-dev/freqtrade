
import logging
from telegram import Bot, ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError

from freqtrade.__init__ import __version__
from freqtrade.constants import DUST_PER_COIN
from freqtrade.enums import RPCMessageType
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
        self._send_msg(message, parse_mode=ParseMode.HTML)

    def _send_msg(self, msg: str, parse_mode: str = ParseMode.HTML) -> None:
        try:
            try:
                self._bot.send_message(
                    self._config['channel']['chat_id'],
                    text=msg,
                    parse_mode=parse_mode
                )
            except NetworkError as network_err:
                # Sometimes the telegram server resets the current connection,
                # if this is the case we send the message again.
                logger.warning(
                    'Telegram NetworkError: %s! Trying one more time.',
                    network_err.message
                )
                self._bot.send_message(
                    self._config['channel']['chat_id'],
                    text=msg,
                    parse_mode=parse_mode
                )
        except TelegramError as telegram_err:
            logger.warning(
                'TelegramError: %s! Giving up on that message.',
                telegram_err.message
            )

    def _format_buy_msg(self, msg: dict) -> str:
        msg['stake_amount_fiat'] = 0.0
        if self._fiat_converter:
            msg['stake_amount_fiat'] = self._fiat_converter.convert_amount(
                msg['stake_amount'], msg['stake_currency'], msg['fiat_currency']
            )
        message = [f"\N{LARGE BLUE DIAMOND} <b>{msg['exchange'].upper()}:{msg['uid']}</b>"]
        message += [f"* <em>Order BUY, {msg['pair']} trade #{msg['trade_id']}</em>"]
        # if msg.get('buy_tag', None):
            # message += [f"- Tag: {msg['buy_tag']}"]
        message += [f"- Amount: {msg['amount']:.8f}"]
        message += [f"- Rate, open: {msg['limit']:.8f}"]
        message += [f"- Rate, current: {msg['current_rate']:.8f}"]

        total = f"- Total: {round_coin_value(msg['stake_amount'], msg['stake_currency'])}"
        if msg.get('fiat_currency', None):
            total += f" | {round_coin_value(msg['stake_amount_fiat'], msg['fiat_currency'])}"
        message += [total]
        return '\n'.join(message)

    def _format_sell_msg(self, msg: dict) -> str:
        msg['amount'] = round(msg['amount'], 8)
        msg['profit_percent'] = round(msg['profit_ratio'] * 100, 2)
        msg['duration'] = msg['close_date'].replace(microsecond=0) - msg['open_date'].replace(microsecond=0)
        msg['duration_min'] = msg['duration'].total_seconds() / 60
        msg['buy_tag'] = msg['buy_tag'] if "buy_tag" in msg.keys() else None
        msg['emoji'] = self._get_sell_emoji(msg)

        msg['profit_extra'] = None
        if (all(prop in msg for prop in ['gain', 'fiat_currency', 'stake_currency']) and self._fiat_converter):
            msg['profit_fiat'] = self._fiat_converter.convert_amount(
                msg['profit_amount'],
                msg['stake_currency'],
                msg['fiat_currency']
            )
            msg['profit_extra'] = ('{stake_currency} {profit_amount:.4f} / {fiat_currency} {profit_fiat:.2f})').format(**msg)

        is_fill = msg['type'] == RPCMessageType.SELL_FILL
        message = ["{emoji} <b>{exchange}:{uid}</b>"]
        message += ["* <em>Order SELL, {pair} trade #{trade_id}</em>"]
        message += ["- Profit: {profit_percent:.2f}%"]
        if msg.get('profit_extra', None):
            message += [f'- {msg["gain"].capitalize()}: {msg["profit_extra"]}']
        # if msg.get('sell_tag', None) or msg.get('buy_tag', None):
            # tag = msg.get('sell_tag', None) or msg.get('buy_tag', None)
            # message += [f"- Tag: {tag}"]
        message += ["- Reason: {sell_reason}"]
        message += ["- Duration: {duration} ({duration_min:.1f}min)"]
        message += ["- Amount: {amount:.8f}"]
        message += ["- Rate, open: {open_rate:.8f}"]
        message += ["- Rate, current: {current_rate:.8f}"]
        message += ["- Rate, close: {limit:.8f}"]
        return '\n'.join(message).format(**msg)


    def compose_message(self, msg: dict, msg_type: RPCMessageType) -> str:
        message = 'NONE'
        msg['uid'] = self._config.get('uid')
        msg['exchange'] = self._config.get('exchange').get('name').upper()
        # for key, value in msg.items():
            # if key in ['open_date', 'close_date']:
                # msg[key] = value if isinstance(value, datetime) else datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f')
            # if key in ['limit', 'amount', 'open_rate', 'close_rate', 'profit_amount', 'profit_ratio', 'stake_amount', 'current_rate']:
                # if value and value not in (None, 'None', ''):
                    # msg[key] = float(value) 
                # else:
                    # msg[key] = 0.0

        if msg_type == RPCMessageType.BUY:
            message = self._format_buy_msg(msg)

        elif msg_type == RPCMessageType.BUY_FILL:
            message = ["\N{SPARKLE} <b>{exchange}:{uid}</b>"]
            message += ["<em>* Order BUY, {pair}, trade #{trade_id} filled</em>"]
            message += ["- Amount: {amount}"]
            message += ["- Rate: {open_rate}"]
            message = '\n'.join(message).format(**msg)


        elif msg_type == RPCMessageType.SELL:
            message = self._format_sell_msg(msg)

        elif msg_type == RPCMessageType.SELL_FILL:
            message = ["\N{SPARKLE} <b>{exchange}:{uid}</b>"]
            message += ["<em>* Order SELL, {pair}, trade #{trade_id} filled</em>"]
            message += ["- Profit: {profit_amount:.4f} {stake_currency}"]
            message += ["- Amount: {amount}"]
            message += ["- Rate: {close_rate}"]
            message = '\n'.join(message).format(**msg)

        elif msg_type in (RPCMessageType.BUY_CANCEL, RPCMessageType.SELL_CANCEL):
            if not msg.get('reason', None):
                msg['reason'] = 'timeout'
            if msg_type == RPCMessageType.SELL_CANCEL and msg.get('sell_reason', None):
                msg['reason'] = msg.get('sell_reason', None)
            msg['side'] = 'BUY' if msg['type'] == RPCMessageType.BUY_CANCEL else 'SELL'
            message = ["\N{WARNING SIGN} {exchange}:{uid}"]
            message += ["<em>* Cancel {side} order, {pair}, trade #{trade_id}</em>"]
            message += ["- Reason: {reason}"]
            message = '\n'.join(message).format(**msg)

        elif msg_type == RPCMessageType.PROTECTION_TRIGGER:
            message = "*Protection* triggered due to {reason}. `{pair}` will be locked until `{lock_end_time}`".format(**msg)

        elif msg_type == RPCMessageType.PROTECTION_TRIGGER_GLOBAL:
            message = "*Protection* triggered due to {reason}. *All pairs* will be locked until `{lock_end_time}`".format(**msg)

        elif msg_type == RPCMessageType.STATUS:
            message = ['\N{GEAR} <b>{exchange}:{uid}</b>']
            # message += ['- Type: {type}']
            message += ['- Status: {status}']
            message = '\n'.join(message).format(**msg)

        elif msg_type == RPCMessageType.WARNING:
            message = '\N{WARNING SIGN} <b>{exchange}:{uid}</b>\n* Warning: {type} {status}'.format(**msg)

        elif msg_type == RPCMessageType.STARTUP:
            status = msg.get('status', '')
            message = '<b>{exchange}:{uid}</b>\n- Type: {type}\n* <em>{status}</em>'.format(**msg)

        # else:
            # raise Warning(f"{msg.get('exchange', None)}:{msg.get('uid', None)} Unknown message type: {msg_type}")

        logger.info(f"Proccessing to message type {msg_type} | body: {msg}")
        return message


    def _get_sell_emoji(self, msg: dict) -> str:
        if float(msg['profit_percent']) >= 5.0:
            return "\N{ROCKET}"
        elif float(msg['profit_percent']) >= 0.0:
            return "\N{EIGHT SPOKED ASTERISK}"
        elif msg['sell_reason'] == "stoploss":
            return"\N{WARNING SIGN}"
        else:
            return "\N{CROSS MARK}"
