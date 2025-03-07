import json
import urllib.request
import os
import re
import unicodedata
from itertools import zip_longest

from .vsnt import FIELDS, CARDS, CSS
from bs4 import BeautifulSoup
from typing import List
from .local_dictionary import LocalDictionary

import mobi
from datetime import datetime
from itertools import islice
from lxml import etree
from charset_normalizer import from_bytes
from ebooklib import epub, ITEM_DOCUMENT
from .sources.WiktionarySource import WiktionarySource
from .sources.GoogleTranslateSource import GoogleTranslateSource
from .sources.LocalDictionarySource import LocalDictionarySource
from .sources.LocalFreqSource import LocalFreqSource
from .sources.LocalAudioSource import LocalAudioSource
from .sources.ForvoAudioSource import ForvoAudioSource
from .models import (LemmaPolicy, DictionarySourceGroup, DisplayMode, SRSNote, 
                     SourceOptions, DictionarySource, FreqSource, AnkiSettings,
                     AudioSource, AudioSourceGroup, WordRecord, WordActionWeights
                     )
from .global_names import settings

def request(action, **params):
    return {'action': action, 'params': params, 'version': 6}

def invoke(action, server, **params):
    requestJson = json.dumps(request(action, **params)).encode('utf-8')
    response = json.load(
        urllib.request.urlopen(
            urllib.request.Request(
                server, requestJson)))
    if len(response) != 2:
        raise Exception('response has an unexpected number of fields')
    if 'error' not in response:
        raise Exception('response is missing required error field')
    if 'result' not in response:
        raise Exception('response is missing required result field')
    if response['error'] is not None:
        raise Exception(response['error'])
    return response['result']


def getDeckList(server) -> list:
    result = invoke('deckNames', server)
    return list(result)


def getNoteTypes(server) -> list:
    result = invoke('modelNames', server)
    return list(result)


def getFields(server, name) -> list:
    result = invoke('modelFieldNames', server, modelName=name)
    return list(result)

def prepareAnkiNoteDict(anki_settings: AnkiSettings, note: SRSNote) -> dict:
    """
    Helper function to create a json to be sent to AnkiConnect
    """
    content = {
        "deckName": anki_settings.deck,
        "modelName": anki_settings.model,
        "fields": {
            anki_settings.word_field: note.word or "",
            anki_settings.sentence_field: note.sentence or "",
            anki_settings.definition1_field: note.definition1 or "",
            anki_settings.definition2_field: note.definition2 or ""
        },
        "tags": []
    }
    if anki_settings.tags:
        content["tags"].extend(anki_settings.tags) # type: ignore
    if note.tags:
        content["tags"].extend(note.tags) # type: ignore
    if note.audio_path:
        content["audio"] = [ 
                    { # type: ignore
                        #"filename": os.path.basename(note.audio_path),
                        "fields": [
                            anki_settings.audio_field
                        ]
                    }
                ]
        # Support both url and path
        if note.audio_path.startswith("http://") or note.audio_path.startswith("https://"):
            content["audio"][0]["url"] = note.audio_path # type: ignore
            content["audio"][0]["filename"] = os.path.basename(note.audio_path) # type: ignore
        else:
            content["audio"][0]["path"] = note.audio_path # type: ignore
            content["audio"][0]["filename"] = os.path.basename(note.audio_path) # type: ignore
    if note.image:
        content["picture"] = [
            { # type: ignore
                "path": note.image,
                "filename": note.image,
                "fields": [
                    anki_settings.image_field
                ]
            }
        ]
    return content

def unix_milliseconds_to_datetime_str(ms: int):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")

def addNote(server, content, allow_duplicates=False) -> int:
    if allow_duplicates:
        content = dict(content) # deepcopy since we are modifying the dict
        content['options'] = {
            "allowDuplicate": True
        }

    result = invoke('addNote', server, note=content)
    return int(result)


def addNotes(server, content) -> List[int]:
    result = invoke('addNotes', server, notes=content)
    return list(result)

def notesInfo(server, notes):
    return invoke('notesInfo', server, notes=notes)

def getVersion(server) -> str:
    result = invoke('version', server)
    return str(result)

def addDefaultModel(server):
    return invoke('createModel', 
        server, 
        modelName="vocabsieve-notes", 
        inOrderFields=FIELDS, 
        css=CSS, 
        cardTemplates=CARDS
        )

def modelFieldNames(server, modelName):
    "Find the field names for a note type"
    return invoke('modelFieldNames', server, modelName=modelName)

def findNotes(server, query):
    return invoke('findNotes', server, query=query)


def guiBrowse(server, query):
    return invoke('guiBrowse', server, query=query)

def is_json(myjson) -> bool:
    if not myjson.startswith("{"):
        return False
    try:
        json_object = json.loads(myjson)
        json_object['word']
        json_object['sentence']
    except ValueError as e:
        return False
    except Exception as e:
        print(e)
        return False
    return True


def failed_lookup(word, settings) -> str:
    return str("<b>Definition for \"" + str(word) + "\" not found.</b><br>Check the following:<br>" +
               "- Language setting (Current: " + settings.value("target_language", 'en') + ")<br>" +
               "- Is the correct word being looked up?<br>" +
               "- Are you connected to the Internet?<br>" +
               "Otherwise, then " + settings.value("dict_source", "Wiktionary (English)") +
               " probably just does not have this word listed.")


def is_oneword(s) -> bool:
    return len(s.split()) == 1

def freq_to_stars(freq_num, lemmatize):
    if freq_num <= 0:
        return ""

    if lemmatize:
        if freq_num <= 1000:
            return "★★★★★"
        elif freq_num <= 3000:
            return "★★★★☆"
        elif freq_num <= 8000:
            return "★★★☆☆"
        elif freq_num <= 20000:
            return "★★☆☆☆"
        elif freq_num <= 40000:
            return "★☆☆☆☆"
        else:
            return "☆☆☆☆☆"
    else:
        if freq_num <= 1500:
            return "★★★★★"
        elif freq_num <= 5000:
            return "★★★★☆"
        elif freq_num <= 15000:
            return "★★★☆☆"
        elif freq_num <= 30000:
            return "★★☆☆☆"
        elif freq_num <= 60000:
            return "★☆☆☆☆"
        else:
            return "☆☆☆☆☆"

def starts_with_cyrillic(s):
    if s:
        return unicodedata.name(s[0]).startswith("CYRILLIC")
    else:
        return s

def remove_ns(s: str) -> str: return str(s).split("}")[-1]

def tostr(s):
    return str(
        from_bytes(
            etree.tostring(
                s,
                encoding='utf8',
                method='text')).best()).strip() 

def ebook2text(path) -> tuple[list[str], dict[int, str]]:
    ch_pos = {}
    chapters = []
    position = 0
    _, ext = os.path.splitext(path)
    if ext == ".txt":
        with open(path, "r") as f:
            return [f.read().replace("\n", " ")], {0: "<content>"}
    if ext in {'.azw', '.azw3', '.kfx', '.mobi'}:
        _, newpath = mobi.extract(path)
        # newpath is either html or epub
        return ebook2text(newpath)
    if ext == '.epub':
        book = epub.read_epub(path)
        for doc in book.get_items_of_type(ITEM_DOCUMENT):
            tree = etree.fromstring(doc.get_content())
            notags = etree.tostring(tree, encoding='utf8', method="text")
            data = str(from_bytes(notags).best()).strip()
            if len(data.splitlines()) < 2:
                continue
            ch_name = data.splitlines()[0]
            content = "\n".join(data.splitlines())
            ch_pos[position] = ch_name
            position += len(content)
            chapters.append(content)
    elif ext == '.fb2':
        with open(path, 'rt') as f:
            data_bytes = f.read().encode()
            tree = etree.fromstring(data_bytes)
        chapters = []
        already_seen = False
        for el in tree:
            tag_nons = remove_ns(el.tag)
            if tag_nons == "body" and not already_seen:
                already_seen = True
                for section in el:
                    current_chapter = ""
                    title = ""
                    for item in section:
                        if remove_ns(item.tag) == "title":
                            title = tostr(item)
                            current_chapter = tostr(item) + "\n\n"
                        else:
                            current_chapter += tostr(item) + "\n"
                    ch_pos[position] = title
                    position += len(current_chapter)
                    chapters.append(current_chapter)
    elif ext == '.html':
        with open(path, 'r', encoding='utf-8') as f:
            c = f.read()
            return [BeautifulSoup(c).getText()], {0: os.path.basename(path)}
    return chapters, ch_pos 

def window(seq, n=2):
    "Returns a sliding window (of width n) over data from the iterable"
    "   s -> (s0,s1,...s[n-1]), (s1,s2,...,sn), ...                   "
    it = iter(seq)
    result = tuple(islice(it, n))
    if len(result) == n:
        yield result
    for elem in it:
        result = result[1:] + (elem,)
        yield result
        
def prettydigits(number):
    return format(number, ',').replace(',', ' ')

def amount_and_percent(amount, total):
    return f"{prettydigits(amount)} ({round(amount / total * 100, 2)}%)" if total else "0 (0%)"


def get_first_number(s: str):
    if re.findall(r'^[^\d]*(\d+)', s):
        return str(re.findall(r'^[^\d]*(\d+)', s)[0])
    else:
        return s

def grouper(iterable, n, *, incomplete='fill', fillvalue=None):
    "Collect data into non-overlapping fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, fillvalue='x') --> ABC DEF Gxx
    # grouper('ABCDEFG', 3, incomplete='strict') --> ABC DEF ValueError
    # grouper('ABCDEFG', 3, incomplete='ignore') --> ABC DEF
    args = [iter(iterable)] * n
    if incomplete == 'fill':
        return zip_longest(*args, fillvalue=fillvalue)
    if incomplete == 'strict':
        return zip(*args, strict=True)
    if incomplete == 'ignore':
        return zip(*args)
    else:
        raise ValueError('Expected fill, strict, or ignore')

def make_freq_source(src_name: str, dictdb: LocalDictionary) -> FreqSource:
    langcode = settings.value("target_language", "en")
    lemmatized = settings.value("lemfreq", True, type=bool)
    return LocalFreqSource(langcode, lemmatized, dictdb, src_name)

def make_audio_source(src_name: str, dictdb: LocalDictionary) -> AudioSource:
    langcode = settings.value("target_language", "en")
    if policy_string:=settings.value(f"audio_lemma_policy"):
        lemma_policy = LemmaPolicy(policy_string)
    else:
        lemma_policy = LemmaPolicy.first_original
    if src_name == "Forvo":
        return ForvoAudioSource(langcode, lemma_policy)
    else:
        custom_dicts = json.loads(settings.value("custom_dicts", "[]"))
        this_dict = next((x for x in custom_dicts if x["name"] == src_name), None)
        if this_dict:
            return LocalAudioSource(langcode, lemma_policy, dictdb, src_name, this_dict["path"])
        else:
            raise Exception("Custom dictionary not found")
    
def make_audio_source_group(src_names: list[str], dictdb: LocalDictionary) -> AudioSourceGroup:
    source_list = []
    for src_name in src_names:
        source_list.append(
            make_audio_source(
                src_name,
                dictdb
            )
        )
    return AudioSourceGroup(source_list)

def make_dict_source(src_name: str, dictdb: LocalDictionary) -> DictionarySource:
    if policy_string:=settings.value(f"{src_name}/lemma_policy"):
        lemma_policy = LemmaPolicy(policy_string)
    else:
        lemma_policy = LemmaPolicy.try_lemma
    if display_mode:=settings.value(f"{src_name}/display_mode"):
        display_mode = DisplayMode(display_mode)
    else:
        display_mode = DisplayMode.markdown_html
    skip_top = settings.value(f"{src_name}/skip_top", 0, type=int)
    collapse_newlines = settings.value(f"{src_name}/collapse_newlines", 0, type=int)

    options = SourceOptions(
        lemma_policy=lemma_policy,
        skip_top=skip_top,
        collapse_newlines=collapse_newlines,
        display_mode=display_mode
    )

    langcode = settings.value("target_language", "en")
    if src_name == "Wiktionary (English)":
        return WiktionarySource(langcode, options)
    elif src_name == "Google Translate":
        return GoogleTranslateSource(
            langcode, 
            options,
            settings.value("gtrans_api", "https://lingva.lunar.icu"), 
            settings.value("gtrans_lang", "en")
        )
    else: # Local, /TODO error handling
        return LocalDictionarySource(langcode, options, dictdb, src_name)


def make_source_group(src_names: list[str], dictdb: LocalDictionary):
    source_list = []
    for src_name in src_names:
        source_list.append(
            make_dict_source(
                src_name,
                dictdb
            )
        )
    return DictionarySourceGroup(source_list)

def compute_word_score(wr: WordRecord, waw: WordActionWeights):
    return (
        waw.seen * wr.n_seen + 
        waw.lookup * wr.n_lookups + 
        waw.anki_mature_ctx * wr.anki_mature_ctx + 
        waw.anki_mature_tgt * wr.anki_mature_tgt + 
        waw.anki_young_ctx * wr.anki_young_ctx + 
        waw.anki_young_tgt * wr.anki_young_tgt
    )

def genPreviewHTML(item: SRSNote) -> str:
    result = f'''<center>{item.sentence}</center>
        <hr>
        <center>
            <b>{item.word}</b>:
            <br>{item.definition1}</center>'''
    if item.definition2 is not None:
        result += f"<hr><center>{item.definition2}</center>"
    return result