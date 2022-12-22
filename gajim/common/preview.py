# This file is part of Gajim.
#
# Gajim is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published
# by the Free Software Foundation; version 3 only.
#
# Gajim is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Gajim. If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from typing import Callable
from typing import Optional

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import ParseResult
import uuid

from gi.repository import Gio
from gi.repository import GLib
from gi.repository import Soup
from nbxmpp.util import convert_tls_error_flags

from gajim.common import app
from gajim.common import configpaths
from gajim.common import regex
from gajim.common.const import MIME_TYPES
from gajim.common.helpers import AdditionalDataDict
from gajim.common.helpers import load_file_async
from gajim.common.helpers import write_file_async
from gajim.common.helpers import get_tls_error_phrases
from gajim.common.helpers import get_account_proxy
from gajim.common.i18n import _
from gajim.common.preview_helpers import aes_decrypt
from gajim.common.preview_helpers import filename_from_uri
from gajim.common.preview_helpers import parse_fragment
from gajim.common.preview_helpers import create_thumbnail
from gajim.common.preview_helpers import split_geo_uri
from gajim.common.preview_helpers import get_previewable_mime_types
from gajim.common.preview_helpers import get_image_paths
from gajim.common.preview_helpers import guess_mime_type
from gajim.common.preview_helpers import pixbuf_from_data
from gajim.common.types import GdkPixbufType

log = logging.getLogger('gajim.c.preview')

IRI_RX = re.compile(regex.IRI)

PREVIEWABLE_MIME_TYPES = get_previewable_mime_types()
mime_types = set(MIME_TYPES)
# Merge both: if it’s a previewable image, it should be allowed
ALLOWED_MIME_TYPES = mime_types.union(PREVIEWABLE_MIME_TYPES)

AudioSampleT = list[tuple[float, float]]


@dataclass
class AudioPreviewState:
    duration: float = 0.0
    position: float = 0.0
    is_eos: bool = False
    speed: float = 1.0
    is_timestamp_positive: bool = True
    samples: AudioSampleT = field(default_factory=list)
    is_audio_analyzed = False


class Preview:
    def __init__(self,
                 uri: str,
                 urlparts: Optional[ParseResult],
                 orig_path: Optional[Path],
                 thumb_path: Optional[Path],
                 size: int,
                 widget: Any,
                 from_us: bool = False,
                 context: Optional[str] = None
                 ) -> None:

        self.id = str(uuid.uuid4())
        self._uri = uri
        self._urlparts = urlparts
        self._filename = filename_from_uri(uri)
        self._widget = widget
        self._from_us = from_us
        self._context = context

        self.account = widget.account
        self.orig_path = orig_path
        self.thumb_path = thumb_path
        self.size = size

        self.thumbnail: Optional[bytes] = None
        self.mime_type: str = ''
        self.file_size: int = 0
        self._received_size: int = 0
        self.download_in_progress = False

        self.info_message: Optional[str] = None

        self._soup_message: Optional[Soup.Message] = None

        self.key: Optional[bytes] = None
        self.iv: Optional[bytes] = None
        if self.is_aes_encrypted and urlparts is not None:
            try:
                self.key, self.iv = parse_fragment(urlparts.fragment)
            except ValueError as err:
                log.error('Parsing fragment for AES decryption '
                          'failed: %s', err)

    @property
    def is_geo_uri(self) -> bool:
        return self._uri.startswith('geo:')

    @property
    def is_web_uri(self) -> bool:
        return not self.is_geo_uri

    @property
    def is_previewable(self) -> bool:
        return self.mime_type in PREVIEWABLE_MIME_TYPES

    @property
    def is_audio(self) -> bool:
        is_allowed = bool(self.mime_type in ALLOWED_MIME_TYPES)
        return is_allowed and self.mime_type.startswith('audio/')

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def from_us(self) -> bool:
        return self._from_us

    @property
    def context(self) -> Optional[str]:
        return self._context

    @property
    def filename(self) -> str:
        return self._filename

    @property
    def soup_message(self) -> Soup.Message:
        return self._soup_message

    @property
    def request_uri(self) -> Optional[str]:
        if self._urlparts is None:
            return ''
        if self.is_aes_encrypted:
            # Remove fragments so we dont transmit it to the server
            urlparts = self._urlparts._replace(scheme='https', fragment='')
            return urlparts.geturl()
        return self._urlparts.geturl()

    @property
    def is_aes_encrypted(self) -> bool:
        if self._urlparts is None:
            return False
        return self._urlparts.scheme == 'aesgcm'

    @property
    def thumb_exists(self) -> bool:
        if self.thumb_path is None:
            return False
        return self.thumb_path.exists()

    @property
    def orig_exists(self) -> bool:
        if self.orig_path is None:
            return False
        return self.orig_path.exists()

    def create_thumbnail(self, data: bytes) -> bool:
        self.thumbnail = create_thumbnail(data, self.size, self.mime_type)
        if self.thumbnail is None:
            self.info_message = _('Creating thumbnail failed')
            log.warning('Creating thumbnail failed for: %s', self.orig_path)
            return False
        return True

    def reset_received_size(self) -> None:
        self._received_size = 0

    def update_widget(self, data: Optional[GdkPixbufType] = None) -> None:
        self._widget.update(self, data)

    def update_progress(self, size: int, message: Soup.Message) -> None:
        self.download_in_progress = True
        self._soup_message = message
        self._received_size += size
        if self.file_size == 0 or self._received_size == 0:
            return

        progress = self._received_size / self.file_size
        self._widget.update_progress(self, progress)


class PreviewManager:
    def __init__(self) -> None:
        self._sessions: dict[
            str,
            tuple[Soup.Session, Optional[Gio.SimpleProxyResolver]]] = {}

        self._orig_dir = Path(configpaths.get('MY_DATA')) / 'downloads'
        self._thumb_dir = Path(configpaths.get('MY_CACHE')) / 'downloads.thumb'

        if GLib.mkdir_with_parents(str(self._orig_dir), 0o700) != 0:
            log.error('Failed to create: %s', self._orig_dir)

        if GLib.mkdir_with_parents(str(self._thumb_dir), 0o700) != 0:
            log.error('Failed to create: %s', self._thumb_dir)

        self._previews: dict[str, Preview] = {}

        # Holds active audio preview sessions
        # for resuming after switching chats
        self._audio_sessions: dict[int, AudioPreviewState] = {}

        # References a stop function for each audio preview, which allows us
        # to stop previews by preview_id, see stop_audio_except(preview_id)
        self._audio_stop_functions: dict[int, Callable[..., None]] = {}

    def get_preview(self, preview_id: str) -> Optional[Preview]:
        return self._previews.get(preview_id)

    def clear_previews(self) -> None:
        self._previews.clear()

    def get_audio_state(self,
                        preview_id: int
                        ) -> AudioPreviewState:

        state = self._audio_sessions.get(preview_id)
        if state is not None:
            return state
        self._audio_sessions[preview_id] = AudioPreviewState()
        return self._audio_sessions[preview_id]

    def register_audio_stop_func(self,
                                 preview_id: int,
                                 stop_func: Callable[..., None]
                                 ) -> None:

        self._audio_stop_functions[preview_id] = stop_func

    def unregister_audio_stop_func(self, preview_id: int) -> None:
        self._audio_stop_functions.pop(preview_id, None)

    def stop_audio_except(self, preview_id: int) -> None:
        # Stops playback of all audio previews except of for preview_id.
        # This makes sure that only one preview is played at the time.
        for id_, stop_func in self._audio_stop_functions.items():
            if id_ != preview_id:
                stop_func()

    def _get_session(self, account: str) -> Soup.Session:
        if account not in self._sessions:
            self._sessions[account] = self._create_session(account)
        return self._sessions[account][0]

    @staticmethod
    def _create_session(account: str) -> tuple[
            Soup.Session, Optional[Gio.SimpleProxyResolver]]:
        session = Soup.Session()
        session.add_feature_by_type(Soup.ContentSniffer)
        session.props.https_aliases = ['aesgcm']
        session.props.ssl_strict = False
        session.props.user_agent = f'Gajim {app.version}'

        proxy = get_account_proxy(account)
        if proxy is None:
            resolver = None
        else:
            resolver = proxy.get_resolver()

        session.props.proxy_resolver = resolver
        return session, resolver

    @staticmethod
    def _accept_uri(urlparts: ParseResult,
                    uri: str,
                    additional_data: AdditionalDataDict) -> bool:
        oob_url = additional_data.get_value('gajim', 'oob_url')

        # geo
        if urlparts.scheme == 'geo':
            return True

        if not urlparts.netloc:
            return False

        # aesgcm
        if urlparts.scheme == 'aesgcm':
            return True

        # http/https
        if urlparts.scheme in ('https', 'http'):
            if app.settings.get('preview_allow_all_images'):
                mime_type = guess_mime_type(uri)
                if mime_type not in MIME_TYPES:
                    return False

                if mime_type == 'application/octet-stream' and uri != oob_url:
                    # guess_mime_type yields 'application/octet-stream' for
                    # paths without suffix. Check oob_url to make sure we
                    # display a preview for files sent via http upload.
                    return False
                return True

            if oob_url is None:
                log.info('No oob url for: %s', uri)
                return False

            if uri != oob_url:
                log.info('uri != oob url: %s != %s', uri, oob_url)
                return False
            return True

        log.info('Unsupported URI scheme: %s', uri)
        return False

    def is_previewable(self,
                       text: str,
                       additional_data: AdditionalDataDict) -> bool:
        if not IRI_RX.fullmatch(text):
            # urlparse removes whitespace (and who knows what else) from URLs,
            # so can't be used for validation.
            return False

        uri = text
        try:
            urlparts = urlparse(uri)
        except Exception:
            return False

        if not self._accept_uri(urlparts, uri, additional_data):
            return False

        if urlparts.scheme == 'geo':
            try:
                split_geo_uri(uri)
            except Exception as err:
                log.info('Bad geo URI %s: %s', uri, err)
                return False

        return True

    def create_preview(self,
                       uri: str,
                       widget: Any,
                       from_us: bool,
                       context: Optional[str] = None
                       ) -> None:
        if uri.startswith('geo:'):
            preview = Preview(uri, None, None, None, 96, widget)
            preview.update_widget()
            self._previews[preview.id] = preview
            return

        preview = self._process_web_uri(uri, widget, from_us, context)
        self._previews[preview.id] = preview

        if not preview.orig_exists:
            if context is not None and not from_us:
                allow_in_public = app.settings.get('preview_anonymous_muc')
                if context == 'public' and not allow_in_public:
                    preview.update_widget()
                    return

            self.download_content(preview)

        elif not preview.thumb_exists:
            load_file_async(preview.orig_path,
                            self._on_orig_load_finished,
                            preview)

        else:
            load_file_async(preview.thumb_path,
                            self._on_thumb_load_finished,
                            preview)

    def _process_web_uri(self,
                         uri: str,
                         widget: Any,
                         from_us: bool,
                         context: Optional[str] = None
                         ) -> Preview:
        urlparts = urlparse(uri)
        size = app.settings.get('preview_size')
        orig_path, thumb_path = get_image_paths(uri,
                                                urlparts,
                                                size,
                                                self._orig_dir,
                                                self._thumb_dir)
        return Preview(uri,
                       urlparts,
                       orig_path,
                       thumb_path,
                       size,
                       widget,
                       from_us,
                       context=context)

    def _on_orig_load_finished(self,
                               data: Optional[bytes],
                               error: Gio.AsyncResult,
                               preview: Preview) -> None:
        if preview.thumb_path is None or preview.orig_path is None:
            return

        if data is None:
            log.error('%s: %s', preview.orig_path.name, error)
            return

        preview.mime_type = guess_mime_type(preview.orig_path, data)
        preview.file_size = os.path.getsize(preview.orig_path)
        if preview.is_previewable:
            if preview.create_thumbnail(data):
                write_file_async(preview.thumb_path,
                                 preview.thumbnail,
                                 self._on_thumb_write_finished,
                                 preview)
        preview.update_widget()

    @staticmethod
    def _on_thumb_load_finished(data: Optional[bytes],
                                error: Gio.AsyncResult,
                                preview: Preview) -> None:

        if preview.thumb_path is None or preview.orig_path is None:
            return

        if data is None:
            log.error('%s: %s', preview.thumb_path.name, error)
            return

        preview.thumbnail = data
        preview.mime_type = guess_mime_type(preview.orig_path, data)
        preview.file_size = os.path.getsize(preview.orig_path)

        try:
            pixbuf = pixbuf_from_data(preview.thumbnail)
        except Exception as err:
            log.error('Unable to load: %s, %s',
                      preview.thumb_path.name,
                      err)
            return

        if pixbuf is None:
            log.error('Unable to load pixbuf')
            return

        preview.update_widget(data=pixbuf)

    def download_content(self,
                         preview: Preview,
                         force: bool = False
                         ) -> None:

        if preview.download_in_progress:
            log.info('Download already in progress')
            return

        preview.reset_received_size()
        if preview.account is None:
            # History Window can be opened without account context
            # This means we can not apply proxy settings
            return
        log.info('Start downloading: %s', preview.request_uri)
        message = Soup.Message.new('GET', preview.request_uri)
        message.connect('starting', self._check_certificate, preview)
        message.connect(
            'content-sniffed', self._on_content_sniffed, preview, force)
        message.connect('got-chunk', self._on_got_chunk, preview)

        session = self._get_session(preview.account)
        session.queue_message(message, self._on_finished, preview)

    def _check_certificate(self,
                           message: Soup.Message,
                           preview: Preview) -> None:
        _https_used, _tls_certificate, tls_errors = message.get_https_status()

        if not app.settings.get('preview_verify_https'):
            return

        if tls_errors:
            phrases = get_tls_error_phrases(convert_tls_error_flags(tls_errors))
            log.warning(
                'TLS verification failed: %s (0x%02x)', phrases, tls_errors)
            session = self._get_session(preview.account)
            session.cancel_message(message, Soup.Status.CANCELLED)
            preview.info_message = _('TLS verification failed: %s') % phrases[0]
            preview.update_widget()

    def _on_content_sniffed(self,
                            message: Soup.Message,
                            type_: str,
                            _params: GLib.HashTable,
                            preview: Preview,
                            force: bool) -> None:
        file_size = message.props.response_headers.get_content_length()
        uri = message.props.uri.to_string(False)
        session = self._get_session(preview.account)
        preview.mime_type = type_
        preview.file_size = file_size

        if type_ not in ALLOWED_MIME_TYPES and not force:
            log.info('Not an allowed content type: %s, %s', type_, uri)
            session.cancel_message(message, Soup.Status.CANCELLED)
            return

        if file_size == 0:
            log.info('File size is unknown (zero) for URL: "%s"', uri)
            session.cancel_message(message, Soup.Status.CANCELLED)
            return

        if file_size > int(app.settings.get('preview_max_file_size')):
            log.info(
                'File size (%s) too big for URL: "%s"',
                file_size, uri)
            if force:
                preview.info_message = None
            else:
                session.cancel_message(message, Soup.Status.CANCELLED)
                preview.info_message = _('Automatic preview disabled '
                                         '(file too big)')

        preview.update_widget()

    def _on_got_chunk(self,
                      message: Soup.Message,
                      chunk: Soup.Buffer,
                      preview: Preview
                      ) -> None:
        preview.update_progress(len(chunk.get_data()), message)

    def _on_finished(self,
                     _session: Soup.Session,
                     message: Soup.Message,
                     preview: Preview
                     ) -> None:

        preview.download_in_progress = False

        if message.status_code != Soup.Status.OK:
            log.warning('Download failed: %s', preview.request_uri)
            status_code = Soup.Status.get_phrase(message.status_code)
            log.warning(status_code)
            preview.reset_received_size()
            if message.status_code != 1:
                # status_code 1: 'Cancelled'
                preview.info_message = _('Download failed (%s)') % status_code
            preview.update_widget()
            return

        preview.info_message = None

        data = message.props.response_body_data.get_data()
        if data is None:
            return

        if preview.is_aes_encrypted:
            if preview.key is not None and preview.iv is not None:
                data = aes_decrypt(preview.key, preview.iv, data)

        if preview.mime_type == 'application/octet-stream':
            if preview.orig_path is not None:
                preview.mime_type = guess_mime_type(preview.orig_path, data)

        write_file_async(preview.orig_path,
                         data,
                         self._on_orig_write_finished,
                         preview)

        if preview.is_previewable:
            if preview.create_thumbnail(data):
                write_file_async(preview.thumb_path,
                                 preview.thumbnail,
                                 self._on_thumb_write_finished,
                                 preview)
            else:
                preview.update_widget()

    @staticmethod
    def _on_orig_write_finished(_result: bool,
                                error: GLib.Error,
                                preview: Preview) -> None:
        if preview.orig_path is None:
            return

        if error is not None:
            log.error('%s: %s', preview.orig_path.name, error)
            return

        log.info('File stored: %s', preview.orig_path.name)
        preview.file_size = os.path.getsize(preview.orig_path)
        if not preview.is_previewable:
            # Don’t update preview if thumb is already displayed,
            # but update preview for audio files
            preview.update_widget()

    @staticmethod
    def _on_thumb_write_finished(_result: bool,
                                 error: GLib.Error,
                                 preview: Preview) -> None:
        if preview.thumb_path is None:
            return

        if error is not None:
            log.error('%s: %s', preview.thumb_path.name, error)
            if not preview.thumb_exists:
                # Generating a preview can fail if the file already exists
                # Only abort if thumbnail has not been stored in preview
                return

        log.info('Thumbnail stored: %s ', preview.thumb_path.name)

        if preview.thumbnail is None:
            return

        try:
            pixbuf = pixbuf_from_data(preview.thumbnail)
        except Exception as err:
            log.error('Unable to load: %s, %s',
                      preview.thumb_path.name,
                      err)
            return

        if pixbuf is None:
            log.error('Unable to load pixbuf')
            return

        preview.update_widget(data=pixbuf)

    def cancel_download(self, preview: Preview) -> None:
        preview.reset_received_size()
        session = self._get_session(preview.account)
        session.cancel_message(preview.soup_message, Soup.Status.CANCELLED)
