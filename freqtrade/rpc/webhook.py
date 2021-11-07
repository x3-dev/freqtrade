"""
This module manages webhook communication
"""
import logging
import requests
from typing import Any, Dict

from freqtrade.enums import RPCMessageType
from freqtrade.rpc import RPC, RPCHandler

logger = logging.getLogger(__name__)
logger.debug('Included module rpc.webhook ...')


class Webhook(RPCHandler):
    """  This class handles all webhook communication """

    def __init__(self, rpc: RPC, config: Dict[str, Any]) -> None:
        """
        Init the Webhook class, and init the super class RPCHandler
        :param rpc: instance of RPC Helper class
        :param config: Configuration object
        :return: None
        """
        super().__init__(rpc, config)

        self._url = self._config['webhook']['url']
        self._format = self._config['webhook'].get('format', 'form')

        if self._format not in ('form', 'json'):
            raise NotImplementedError(
                f'Unknown webhook format `{self._format}`, possible values are `form` (default) and `json`'
            )

    def cleanup(self) -> None:
        """
        Cleanup pending module resources.
        This will do nothing for webhooks, they will simply not be called anymore
        """
        pass

    def send_msg(self, msg: Dict[str, Any]) -> None:
        """ Send a message to telegram channel """
        try:
            if msg['type'] == RPCMessageType.BUY:
                valuedict = self._config['webhook'].get('webhookbuy', None)
            elif msg['type'] == RPCMessageType.BUY_CANCEL:
                valuedict = self._config['webhook'].get('webhookbuycancel', None)
            elif msg['type'] == RPCMessageType.BUY_FILL:
                valuedict = self._config['webhook'].get('webhookbuyfill', None)
            elif msg['type'] == RPCMessageType.SELL:
                valuedict = self._config['webhook'].get('webhooksell', None)
            elif msg['type'] == RPCMessageType.SELL_FILL:
                valuedict = self._config['webhook'].get('webhooksellfill', None)
            elif msg['type'] == RPCMessageType.SELL_CANCEL:
                valuedict = self._config['webhook'].get('webhooksellcancel', None)
            elif msg['type'] in (RPCMessageType.STATUS, RPCMessageType.STARTUP, RPCMessageType.WARNING):
                valuedict = self._config['webhook'].get('webhookstatus', None)
            else:
                raise NotImplementedError(f'Unknown message type: {msg["type"]}')
            if not valuedict:
                logger.info(f'Message type {msg["type"]} not configured for webhooks')
                return

            payload = {key: value.format(**msg) for (key, value) in valuedict.items()}
            if 'type' not in payload.keys():
                payload['type'] = msg['type']

            self._send_msg(payload)
        except KeyError as exc:
            logger.exception(f"Problem calling Webhook. Please check your webhook configuration. Exception: {exc}")

    def _send_msg(self, payload: dict) -> None:
        """ do the actual call to the webhook """
        if 'exchange' not in payload.keys():
            payload['exchange'] = self._config['exchange'].get('name')
        try:
            config = dict()
            if self._format == 'form':
                config['data'] = payload
            elif self._format == 'json':
                config['json'] = payload
            else:
                raise NotImplementedError(f'Unknown format: {self._format}')

            if self._config['webhook'].get('auth', {}):
                config['auth'] = (
                    self._config['webhook'].get('auth').get('user'),
                    self._config['webhook'].get('auth').get('password')
                )
            requests.post(self._url, **config)
        except requests.RequestException as err:
            logger.warning(f"Could not call webhook url. Exception: {err}")
