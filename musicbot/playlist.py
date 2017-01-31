import datetime
import traceback
from collections import deque
from itertools import islice
from random import shuffle

from .utils import get_header
from .entry import URLPlaylistEntry
from .exceptions import ExtractionError, WrongEntryTypeError
from .lib.event_emitter import EventEmitter

# lyrics parsing
from urllib.request import urlopen
from urllib.parse import urlencode
from bs4 import BeautifulSoup

class Playlist(EventEmitter):
    """
        A playlist is manages the list of songs that will be played.
    """

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.loop = bot.loop
        self.downloader = bot.downloader
        self.entries = deque()

    def __iter__(self):
        return iter(self.entries)

    def shuffle(self):
        shuffle(self.entries)

    def clear(self):
        self.entries.clear()

    def get_first_result(self, url):
        response = urlopen(url)
        html_doc = response.read()
        soup = BeautifulSoup(html_doc, 'html.parser')
        span = soup.find_all('a')
        #moijm has different versions of lyrics base on broswer i think, goes up 1+3(i+1)
        for x in range(0, 5):
            index = 3 + (3 * x)
            print(span[index]['href'])
            x += 1

        """for elem in span:
            print(elem['href']) """
        return "https://mojim.com" + span[3]['href']

    def convert_to_string(self, string):
        middle = string[2:-1]
        ret = ""
        i = 0
        while i < len(middle):
            if (middle[i] == '\\'):
                ret += '%'
                i += 2
            else :
                ret += middle[i]
                i += 1
        print("ret is : " + ret)
        return ret

    def is_song_chinese(self):
        for char in self.bot.message[6:]:
            if char < u'\u4e00' or char > u'\u9fff':
                return False

        return True

    def parse_lyrics(self):
        if len(self.bot.message) <= 5:
            return "no lyrics available"
        else:
            if self.is_song_chinese() != True:
                return "no lyrics available"
            
        print("utf-g: " + str(self.bot.message[6:].encode('utf-8')))
        song_name = self.convert_to_string(str(self.bot.message[6:].encode('utf-8')))
        search_url = "https://mojim.com/" + song_name + ".html?u3"
        print("search_url is : " + search_url)


        """ implementation:
            if (mandarin) {
                use mojim to scrap lyrics
            } else if (eng ) {
                use musixmatch api to get lyrics
            } else if (other language.. etc){
                add other sources.. etc
            }
        """
        # todo: need to figure out where the bot gets the input from discord caht
        #       maybe visit discord api, if cant find it and just make your own listener
        # once we have listener, use that to make the search url, then get first result
        # then the parsing shld be fine

        url = self.get_first_result(search_url)
        print("url is : " + url)
        response = urlopen(url)
        html_doc = response.read()
        soup = BeautifulSoup(html_doc, 'html.parser')
        lyrics_body = soup.find_all('dl', {'class', 'fsZx1'})
        lyrics_text = ""
        for item in lyrics_body:
            lyrics_text += item.get_text()

        return lyrics_text

    def format_lyrics(self, lyrics):
        return lyrics

    async def add_entry(self, song_url, **meta):
        """
            Validates and adds a song_url to be played. This does not start the download of the song.

            Returns the entry & the position it is in the queue.

            :param song_url: The song url to add to the playlist.
            :param meta: Any additional metadata to add to the playlist entry.
        """

        try:
            info = await self.downloader.extract_info(self.loop, song_url, download=False)
        except Exception as e:
            raise ExtractionError('Could not extract information from {}\n\n{}'.format(song_url, e))

        if not info:
            raise ExtractionError('Could not extract information from %s' % song_url)

        # TODO: Sort out what happens next when this happens
        if info.get('_type', None) == 'playlist':
            raise WrongEntryTypeError("This is a playlist.", True, info.get('webpage_url', None) or info.get('url', None))

        if info['extractor'] in ['generic', 'Dropbox']:
            try:
                # unfortunately this is literally broken
                # https://github.com/KeepSafe/aiohttp/issues/758
                # https://github.com/KeepSafe/aiohttp/issues/852
                content_type = await get_header(self.bot.aiosession, info['url'], 'CONTENT-TYPE')
                print("Got content type", content_type)

            except Exception as e:
                print("[Warning] Failed to get content type for url %s (%s)" % (song_url, e))
                content_type = None

            if content_type:
                if content_type.startswith(('application/', 'image/')):
                    if '/ogg' not in content_type:  # How does a server say `application/ogg` what the actual fuck
                        raise ExtractionError("Invalid content type \"%s\" for url %s" % (content_type, song_url))

                elif not content_type.startswith(('audio/', 'video/')):
                    print("[Warning] Questionable content type \"%s\" for url %s" % (content_type, song_url))

        # helper method for lyrics from moijm
        lyrics = self.parse_lyrics()
        self.format_lyrics(lyrics)
        #print("in playlist.py the lyrics from parse lyrics: " + lyrics)
                    
        entry = URLPlaylistEntry(
            self,
            song_url,
            info.get('title', 'Untitled'),
            lyrics,
            info.get('duration', 0) or 0,
            self.downloader.ytdl.prepare_filename(info),
            **meta
        )
        self._add_entry(entry)
        return entry, len(self.entries)

    async def import_from(self, playlist_url, **meta):
        """
            Imports the songs from `playlist_url` and queues them to be played.

            Returns a list of `entries` that have been enqueued.

            :param playlist_url: The playlist url to be cut into individual urls and added to the playlist
            :param meta: Any additional metadata to add to the playlist entry
        """
        position = len(self.entries) + 1
        entry_list = []

        try:
            info = await self.downloader.safe_extract_info(self.loop, playlist_url, download=False)
        except Exception as e:
            raise ExtractionError('Could not extract information from {}\n\n{}'.format(playlist_url, e))

        if not info:
            raise ExtractionError('Could not extract information from %s' % playlist_url)

        # Once again, the generic extractor fucks things up.
        if info.get('extractor', None) == 'generic':
            url_field = 'url'
        else:
            url_field = 'webpage_url'

        baditems = 0
        for items in info['entries']:
            if items:
                try:
                    entry = URLPlaylistEntry(
                        self,
                        items[url_field],
                        items.get('title', 'Untitled'),
                        items.get('duration', 0) or 0,
                        self.downloader.ytdl.prepare_filename(items),
                        **meta
                    )

                    self._add_entry(entry)
                    entry_list.append(entry)
                except:
                    baditems += 1
                    # Once I know more about what's happening here I can add a proper message
                    traceback.print_exc()
                    print(items)
                    print("Could not add item")
            else:
                baditems += 1

        if baditems:
            print("Skipped %s bad entries" % baditems)

        return entry_list, position

    async def async_process_youtube_playlist(self, playlist_url, **meta):
        """
            Processes youtube playlists links from `playlist_url` in a questionable, async fashion.

            :param playlist_url: The playlist url to be cut into individual urls and added to the playlist
            :param meta: Any additional metadata to add to the playlist entry
        """

        try:
            info = await self.downloader.safe_extract_info(self.loop, playlist_url, download=False, process=False)
        except Exception as e:
            raise ExtractionError('Could not extract information from {}\n\n{}'.format(playlist_url, e))

        if not info:
            raise ExtractionError('Could not extract information from %s' % playlist_url)

        gooditems = []
        baditems = 0
        for entry_data in info['entries']:
            if entry_data:
                baseurl = info['webpage_url'].split('playlist?list=')[0]
                song_url = baseurl + 'watch?v=%s' % entry_data['id']

                try:
                    entry, elen = await self.add_entry(song_url, **meta)
                    gooditems.append(entry)
                except ExtractionError:
                    baditems += 1
                except Exception as e:
                    baditems += 1
                    print("There was an error adding the song {}: {}: {}\n".format(
                        entry_data['id'], e.__class__.__name__, e))
            else:
                baditems += 1

        if baditems:
            print("Skipped %s bad entries" % baditems)

        return gooditems

    async def async_process_sc_bc_playlist(self, playlist_url, **meta):
        """
            Processes soundcloud set and bancdamp album links from `playlist_url` in a questionable, async fashion.

            :param playlist_url: The playlist url to be cut into individual urls and added to the playlist
            :param meta: Any additional metadata to add to the playlist entry
        """

        try:
            info = await self.downloader.safe_extract_info(self.loop, playlist_url, download=False, process=False)
        except Exception as e:
            raise ExtractionError('Could not extract information from {}\n\n{}'.format(playlist_url, e))

        if not info:
            raise ExtractionError('Could not extract information from %s' % playlist_url)

        gooditems = []
        baditems = 0
        for entry_data in info['entries']:
            if entry_data:
                song_url = entry_data['url']

                try:
                    entry, elen = await self.add_entry(song_url, **meta)
                    gooditems.append(entry)
                except ExtractionError:
                    baditems += 1
                except Exception as e:
                    baditems += 1
                    print("There was an error adding the song {}: {}: {}\n".format(
                        entry_data['id'], e.__class__.__name__, e))
            else:
                baditems += 1

        if baditems:
            print("Skipped %s bad entries" % baditems)

        return gooditems

    def _add_entry(self, entry):
        self.entries.append(entry)
        self.emit('entry-added', playlist=self, entry=entry)

        if self.peek() is entry:
            entry.get_ready_future()

    async def get_next_entry(self, predownload_next=True):
        """
            A coroutine which will return the next song or None if no songs left to play.

            Additionally, if predownload_next is set to True, it will attempt to download the next
            song to be played - so that it's ready by the time we get to it.
        """
        if not self.entries:
            return None

        entry = self.entries.popleft()

        if predownload_next:
            next_entry = self.peek()
            if next_entry:
                next_entry.get_ready_future()

        return await entry.get_ready_future()

    def peek(self):
        """
            Returns the next entry that should be scheduled to be played.
        """
        if self.entries:
            return self.entries[0]

    async def estimate_time_until(self, position, player):
        """
            (very) Roughly estimates the time till the queue will 'position'
        """
        estimated_time = sum([e.duration for e in islice(self.entries, position - 1)])

        # When the player plays a song, it eats the first playlist item, so we just have to add the time back
        if not player.is_stopped and player.current_entry:
            estimated_time += player.current_entry.duration - player.progress

        return datetime.timedelta(seconds=estimated_time)

    def count_for_user(self, user):
        return sum(1 for e in self.entries if e.meta.get('author', None) == user)


