"""Conversation objects."""

import logging
from tornado import gen

from hangups import parsers, exceptions, event, user

logger = logging.getLogger(__name__)


class Conversation(object):
    """Wrapper around Client for working with a single chat conversation."""

    def __init__(self, client, conv_state, user_list):
        """Initialize a new Conversation from a ClientConversationState."""
        self._client = client  # Client
        self._user_list = user_list  # UserList
        self._id = conv_state.conversation_id.id_  # str
        self._active_participant_ids = [
            user.UserID(chat_id=part.id_.chat_id, gaia_id=part.id_.gaia_id)
            for part in conv_state.conversation.participant_data
        ]  # [UserID]
        self._last_modified = parsers.from_timestamp(
            conv_state.conversation.self_conversation_state.sort_timestamp
        )  # datetime
        self._name = conv_state.conversation.name # str or None
        self._chat_messages = []  # [ChatMessage]
        for ev in conv_state.event:
            try:
                if ev.chat_message is not None:
                    self._chat_messages.append(parsers.parse_chat_message(ev))
            except exceptions.ParseError as e:
                logger.warning('Failed to parse chat message: {}'.format(e))

        # Event fired when a new message arrives with arguments (chat_message).
        self.on_message = event.Event('Conversation.on_message')
        # Event fired when a user starts or stops typing with arguments
        # (typing_message).
        self.on_typing = event.Event('Conversation.on_typing')

    @property
    def id_(self):
        """Return the Conversation's ID."""
        return self._id

    @property
    def users(self):
        """Return the list of Users currently participating."""
        return [self._user_list.get_user(id_)
                for id_ in self._active_participant_ids]

    def get_user(self, user_id):
        """Return the User instance with the given UserID."""
        return self._user_list.get_user(user_id)

    @property
    def name(self):
        """Return conversation's custom name, or None if it was not renamed."""
        return self._name

    @property
    def last_modified(self):
        """Return the timestamp of when the conversation was last modified."""
        return self._last_modified

    @property
    def chat_messages(self):
        """Return a list of ChatMessages, sorted oldest to newest."""
        return list(self._chat_messages)

    @gen.coroutine
    def send_message(self, text):
        """Send a message to this conversation.

        text may not be empty.

        Raises hangups.NetworkError if the message can not be sent.
        """
        yield self._client.sendchatmessage(self._id, text)


class ConversationList(object):
    """Wrapper around Client that maintains a list of Conversations."""

    def __init__(self, client, conv_states, user_list):
        self._client = client
        self._conv_dict = {}  # {conv_id: Conversation}

        # Initialize the list of conversation from Client's list of
        # ClientConversationStates.
        for conv_state in conv_states:
            conv_id = conv_state.conversation_id.id_
            self._conv_dict[conv_id] = Conversation(self._client, conv_state,
                                                    user_list)

        self._client.on_state_update.add_observer(self._on_state_update)
        self._client.on_event_notification.add_observer(
            self._on_event_notification
        )

        # Event fired when a new message arrives with arguments (chat_message).
        self.on_message = event.Event('ConversationList.on_message')
        # Event fired when a user starts or stops typing with arguments
        # (typing_message).
        self.on_typing = event.Event('ConversationList.on_typing')

    def get_all(self):
        """Return list of all Conversations."""
        return list(self._conv_dict.values())

    def get(self, conv_id):
        """Return a Conversation from its ID.

        Raises KeyError if the conversation ID is invalid.
        """
        return self._conv_dict[conv_id]

    def _on_state_update(self, state_update):
        """Receive a ClientStateUpdate and fan out to Conversations."""
        if state_update.typing_notification is not None:
            self._handle_set_typing_notification(
                state_update.typing_notification
            )
        if state_update.event_notification is not None:
            self._on_event_notification(state_update.event_notification)

    def _on_event_notification(self, event_notification):
        """Receive a ClientEventNofication and fan out to Conversations."""
        if event_notification.event.chat_message is not None:
            self._handle_chat_message(event_notification.event)

    def _handle_chat_message(self, event_):
        """Receive ClientEvent and update the conversation with messages."""
        conv_id = event_.conversation_id.id_
        conv = self._conv_dict.get(conv_id, None)
        if conv is not None:
            try:
                if event_.chat_message is not None:
                    res = parsers.parse_chat_message(event_)
                else:
                    res = None
            except exceptions.ParseError as e:
                logger.warning('Failed to parse chat message: {}'.format(e))
            else:
                if res is not None:
                    self.on_message.fire(res)
                    conv.on_message.fire(res)
        else:
            logger.warning('Received ClientEvent for unknown conversation {}'
                           .format(conv_id))


    def _handle_set_typing_notification(self, set_typing_notification):
        """Receive ClientSetTypingNotification and update the conversation."""
        conv_id = set_typing_notification.conversation_id.id_
        conv = self._conv_dict.get(conv_id, None)
        if conv is not None:
            res = parsers.parse_typing_status_message(set_typing_notification)
            self.on_typing.fire(res)
            conv.on_typing.fire(res)
        else:
            logger.warning('Received ClientSetTypingNotification for '
                           'unknown conversation {}'.format(conv_id))