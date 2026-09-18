# -*- coding: utf-8 -*-

import json
import subprocess
import sys
import time
from pathlib import Path

import requests
from gi.repository import Gdk, Gtk, Pango

from zim.plugins import PluginClass, PluginManager
from zim.actions import action
from zim.gui.mainwindow import MainWindowExtension
from zim.config import ConfigManager

SHARED_ZOTERO_DIR = Path.home() / '.local' / 'share' / 'zim' / 'zotero_local_api'

ZOTERO_EXECUTABLE = Path.home() / '.Zotero_64' / 'zotero'
ZOTERO_API_URL = 'http://127.0.0.1:23119/api/'

PREFIX_OVERRIDES_FILE = (
    Path.home()
    / '.config'
    / 'zim'
    / 'zotero'
    / 'prefixes.json'
)

ZOTERO_CONFIG_FILE = (
    Path.home()
    / '.config'
    / 'zim'
    / 'zotero'
    / 'config.json'
)

ZOTERO_START_TIMEOUT = 15
ZOTERO_CHECK_INTERVAL = 0.5

SEARCH_PAGE_SIZE = 50

if str(SHARED_ZOTERO_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_ZOTERO_DIR))

from zotero_local_api import ZoteroLocalAPI, ZoteroAPIError

def _apply_zim_text_font(textview):
    """
    Применяет к Gtk.TextView шрифт,
    заданный в настройках текста Zim.
    """

    text_style = ConfigManager.get_config_dict(
        'style.conf'
    )

    font_name = (
        text_style['TextView'].get(
            'font'
        )
    )

    if not font_name:
        textview.modify_font(
            None
        )
        return

    font = Pango.FontDescription(
        font_name
    )

    textview.modify_font(
        font
    )

def zotero_api_available():
    try:
        response = requests.get(
            ZOTERO_API_URL,
            headers={'User-Agent': 'Zim-Zotero/1.0'},
            timeout=1,
        )
        return response.status_code < 500
    except requests.RequestException:
        return False

def ensure_zotero_running(timeout=ZOTERO_START_TIMEOUT):
    if zotero_api_available():
        return True


    if not ZOTERO_EXECUTABLE.is_file():
        return False

    try:
        subprocess.Popen(
            [str(ZOTERO_EXECUTABLE)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if zotero_api_available():
            return True

        time.sleep(ZOTERO_CHECK_INTERVAL)

    return False

def _load_zotero_config():
    """
    Загружает конфигурацию плагина Zim-Zotero.

    Если файл отсутствует или содержит некорректный JSON,
    возвращается пустой словарь.
    """

    if not ZOTERO_CONFIG_FILE.is_file():
        return {}

    try:
        with ZOTERO_CONFIG_FILE.open(
            'r',
            encoding='utf-8',
        ) as handle:

            data = json.load(handle)

        if not isinstance(data, dict):
            return {}

        return data

    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {}

def _load_index_sort_spec():

    config = _load_zotero_config()

    value = config.get(
        'index_sort_spec'
    )

    if value is None:
        return list(DEFAULT_SORT_SPEC)

    try:
        sort_spec = [
            (field, reverse)
            for field, reverse in value
        ]

        _validate_sort_spec(
            sort_spec
        )

        return sort_spec

    except (
        TypeError,
        ValueError,
    ):
        return list(DEFAULT_SORT_SPEC)

def _save_index_sort_spec(sort_spec):

    _validate_sort_spec(
        sort_spec
    )

    config = _load_zotero_config()

    config['index_sort_spec'] = [
        [field, reverse]
        for field, reverse in sort_spec
    ]

    ZOTERO_CONFIG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with ZOTERO_CONFIG_FILE.open(
        'w',
        encoding='utf-8',
    ) as handle:

        json.dump(
            config,
            handle,
            ensure_ascii=False,
            indent=4,
        )

def _get_bibliography_notebook_name():
    """
    Возвращает имя Zim notebook, используемого
    как библиография.
    """

    config = _load_zotero_config()

    return (
        config.get('bibliography_notebook', '')
        or ''
    ).strip()


def _get_bibliography_notebook():
    """
    Возвращает объект Zim notebook, используемого
    как библиография.

    Имя notebook берётся из config.json.
    """

    from zim.notebook import (
        build_notebook,
        resolve_notebook,
    )

    name = _get_bibliography_notebook_name()

    if not name:
        raise RuntimeError(
            'В config.json не указан '
            'bibliography_notebook.'
        )

    info = resolve_notebook(name)

    if info is None:
        raise RuntimeError(
            f'Zim notebook "{name}" не найден.'
        )

    notebook, _ = build_notebook(info)

    return notebook

def _is_material_page_name(name):
    """
    Проверяет, соответствует ли имя страницы формату Material ID.

    Формат:

        AUTHOR-TITLE-TYPE-LEVEL3-LEVEL4-NUMBER

    Например:

        МК-КЛ-КН-01-000232-01

    Полное имя страницы может содержать путь Zim,
    поэтому перед проверкой берётся только basename.
    """

    basename = name.rsplit(
        ':',
        1
    )[-1]

    parts = basename.split('-')

    if len(parts) != 6:
        return False

    return all(
        part.strip()
        for part in parts
    )

def _extract_fragment(page):
    """
    Извлекает содержимое секции:

        ===== Фрагмент =====

    из Material page.

    Если секция отсутствует, возвращает None.
    Если секция существует, но пуста, возвращает
    пустую строку.
    """

    lines = page.dump('wiki')

    start_marker = '===== Фрагмент =====\n'

    start = None

    for index, line in enumerate(lines):

        if line == start_marker:
            start = index + 1
            break

    if start is None:
        return None

    fragment_lines = []

    for line in lines[start:]:

        if (
            line.startswith('===== ')
            and line.rstrip().endswith(' =====')
        ):
            break

        fragment_lines.append(line)

    return ''.join(fragment_lines).strip()

def _find_material_pages(notebook):
    """
    Возвращает список Material pages в notebook.

    Поиск выполняется по всему дереву notebook.
    Material определяется по формату имени страницы.
    """

    from zim.notebook import Path

    material_pages = []

    for record in notebook.pages.walk(Path('')):

        if _is_material_page_name(record.name):
            material_pages.append(record.name)

    return material_pages

def _extract_material_record(notebook, material_name):
    """
    Извлекает структурированную информацию из Material page.

    Возвращает словарь:

        name
        material_id
        zotero_key
        author
        title
        item_type
        date
        place
        location
        fragment

    Если страница не существует или не является Material,
    возвращается None.
    """

    from zim.notebook import Path

    page = notebook.get_page(
        Path(material_name)
    )

    if not page.exists():
        return None

    basename = material_name.rsplit(
        ':',
        1
    )[-1]

    if not _is_material_page_name(
        basename
    ):
        return None

    parent_path = material_name.rsplit(
        ':',
        1
    )[0]

    work = parent_path.rsplit(
        ':',
        1
    )[-1]

    lines = page.dump('wiki')

    record = {
        'name': material_name,
        'material_id': basename,
        'work': work,
        'zotero_key': '',
        'author': '',
        'title': '',
        'item_type': '',
        'date': '',
        'place': '',
        'location': '',
        'fragment': '',
    }

    section = None
    source_lines = []
    place_lines = []
    fragment_lines = []

    for line in lines:

        stripped = line.rstrip()

        if stripped == '===== Источник =====':
            section = 'source'
            continue

        if stripped == '===== Место =====':
            section = 'place'
            continue

        if stripped == '===== Фрагмент =====':
            section = 'fragment'
            continue

        if (
            stripped.startswith('===== ')
            and stripped.endswith(' =====')
        ):
            section = None
            continue

        if section == 'source':
            source_lines.append(
                line.rstrip('\n')
            )

        elif section == 'place':
            place_lines.append(
                line.rstrip('\n')
            )

        elif section == 'fragment':
            fragment_lines.append(
                line.rstrip('\n')
            )

    for line in source_lines:

        if line.startswith('Автор:'):
            record['author'] = (
                line[len('Автор:'):].strip()
            )

        elif line.startswith('Название:'):
            record['title'] = (
                line[len('Название:'):].strip()
            )

        elif line.startswith('Тип:'):
            record['item_type'] = (
                line[len('Тип:'):].strip()
            )

        elif line.startswith('Дата:'):
            record['date'] = (
                line[len('Дата:'):].strip()
            )

        elif line.startswith('Zotero:'):
            marker = (
                'zotero://select/library/items/'
            )

            if marker in line:

                key = line.split(
                    marker,
                    1
                )[1]

                key = key.split(
                    '|',
                    1
                )[0]

                record['zotero_key'] = (
                    key.rstrip(']')
                )

    place_values = []

    for line in place_lines:

        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith('* '):
            place_values.append(
                stripped[2:].strip()
            )

        elif stripped.startswith('Страница:'):
            record['location'] = (
                stripped[len('Страница:'):].strip()
            )

    record['place'] = ' → '.join(
        place_values
    )

    record['fragment'] = ''.join(
        fragment_lines
    ).strip()

    return record

# def _find_material_records(notebook):
    # """
    # Возвращает список структурированных записей всех Material pages
    # в notebook.

    # Каждая запись создаётся функцией
    # _extract_material_record().
    # """

    # material_names = _find_material_pages(
        # notebook
    # )

    # records = []

    # for material_name in material_names:

        # record = _extract_material_record(
            # notebook,
            # material_name,
        # )

        # if record is not None:
            # records.append(
                # record
            # )

    # return records

def _material_index_link(record, link_type):
    """
    Формирует ссылку для Material Index.

    link_type:
        'author'  — страница автора
        'work'    — страница произведения
        'material' — конкретная Material page
    """

    material_name = record['name']

    parent_path = material_name.rsplit(
        ':',
        1
    )[0]

    if link_type == 'material':

        return (
            f'[[{material_name}|'
            f'{record["fragment"]}]]'
        )

    if link_type == 'work':

        return (
            f'[[{parent_path}|'
            f'{record["work"]}]]'
        )

    if link_type == 'author':

        author_path = parent_path.rsplit(
            ':',
            1
        )[0]

        return (
            f'[[{author_path}|'
            f'{record["author"]}]]'
        )

    raise ValueError(
        f'Неизвестный тип ссылки: {link_type}'
    )

def _material_zim_path(material_name):

    ZimPath = __import__(
        'zim.notebook',
        fromlist=['Path']
    ).Path

    return ZimPath(
        material_name
    )

# # QUESTION !
# def _prepare_material_index_records(notebook):
    # """
    # Подготавливает записи Material для Material Index.

    # В Index используются поля:

        # Автор
        # Произведение
        # Тип
        # Дата
        # Место
        # Фрагмент

    # Material ID и Zotero Key остаются в записи
    # как технические данные для последующих действий.
    # """

    # records = _find_material_records(
        # notebook
    # )

    # index_records = []

    # for record in records:

        # fragment = (
            # record['fragment']
            # or ''
        # ).strip()

        # if fragment:

            # fragment_preview = (
                # fragment.replace(
                    # '\n',
                    # ' '
                # ).strip()
            # )

        # else:

            # fragment_preview = 'вложение'

        # index_records.append(
            # {
                # 'author': record['author'],
                # 'work': record['work'],
                # 'item_type': record['item_type'],
                # 'date': record['date'],
                # 'place': record['place'],
                # 'fragment': fragment_preview,

                # # Технические поля.
                # 'name': record['name'],
                # 'material_id': record['material_id'],
                # 'zotero_key': record['zotero_key'],
            # }
        # )

    # return index_records

def _get_material_fragment(notebook, material_name):
    """
    Возвращает Fragment конкретного Material page.

    material_name — полный путь Zim page, например:

        Маркс Карл:Немецкая идеология:
        МК-НИ-КН-00-000031-01

    Если Material page не существует или Fragment
    отсутствует, возвращается None.
    """

    from zim.notebook import Path

    page = notebook.get_page(
        Path(material_name)
    )

    return _extract_fragment(page)

def _material_link(material_name):
    """
    Формирует Zim-ссылку на Material page.

    Например:

        Маркс Карл:Немецкая идеология:
        МК-НИ-КН-00-000031-01

    превращается в:

        [[cite?Маркс Карл:Немецкая идеология:
        МК-НИ-КН-00-000031-01|Материал]]
    """

    return (
        f'[[cite?{material_name}|Материал]]'
    )

def _format_location_tree(levels):
    """
    Преобразует список уровней Места в Zim-разметку
    вложенного маркированного списка.

    Например:

        [
            '1. Товар и деньги',
            '3. Деньги, или обращение товаров',
            '2. Средство обращения',
            'b) Обращение денег',
        ]

    превращается в:

        * 1. Товар и деньги
          * 3. Деньги, или обращение товаров
            * 2. Средство обращения
              * b) Обращение денег
    """

    lines = []

    for level, value in enumerate(levels):

        value = value.strip()

        if not value:
            continue

        indent = '\t' * level

        lines.append(
            f'{indent}* {value}'
        )

    return '\n'.join(lines)

def _insert_material_fragment(
    notebook,
    material_name,
    textview,
):
    """
    Вставляет Fragment выбранного Material page
    и ссылку на этот Material в текущую позицию курсора.

    Формат вставки:

        Fragment

        [[cite?...|Материал]]
    """

    fragment = _get_material_fragment(
        notebook,
        material_name,
    )

    if fragment is None:
        raise RuntimeError(
            'Material page не существует '
            'или секция "Фрагмент" отсутствует.'
        )

    link = _material_link(
        material_name
    )

    text = (
        fragment
        + '\n\n'
        + link
    )

    buffer = textview.get_buffer()

    buffer.insert_at_cursor(
        text
    )


def open_item_in_zotero(key):
    if not key:
        return False

    try:
        subprocess.Popen(
            [
                str(ZOTERO_EXECUTABLE),
                '-url',
                f'zotero://select/library/items/{key}',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True

    except OSError:
        return False


# =============================================================
# Модель типов материалов
#
# Каждый тип Zotero (itemType) описывается конфигурацией:
#
#   label            — отображаемое название типа
#   type_code        — код типа для ID (уровень 2)
#   creator_types    — предпочтительные creatorType для поля "Автор",
#                       в порядке приоритета; если ни один не найден,
#                       берётся первый creator из списка
#   level3_label     — подпись поля "крупное деление источника"
#                       (том/часть/эпизод) в разделе "Источник";
#                       None, если у типа нет такого уровня
#   level4_label     — подпись поля "точная локализация"
#                       (страница/тайм-код/раздел) в разделе "Место"
#   level4_kind       — 'number' или 'timecode'; определяет способ
#                       нормализации значения уровня 4 в ID.
#                       Ширина результата одинакова для всех типов
#                       (см. LEVEL4_WIDTH) — так предпоследняя группа
#                       цифр в ID выглядит однородно независимо от типа.
#   source_fields     — доп. поля раздела "Источник":
#                       список (key, label, zotero_field_или_None);
#                       если zotero_field указан, поле предзаполняется
#                       из item_data, иначе вводится вручную.
#                       URL сюда сознательно не включается: он уже
#                       хранится в Zotero, и повторение его в тексте
#                       страницы Zim создало бы два источника истины,
#                       которые могут разойтись при редактировании.
#   Раздел "Место" формируется динамически:
#   произвольное количество уровней задаётся пользователем
#   при создании Material.
# =============================================================

# Единая ширина (в цифрах) уровня 4 для всех типов записей.
# Тайм-код (ЧЧММСС) занимает 6 цифр — это и берётся как общий
# ориентир, чтобы "Страница"/"Номер раздела" у других типов
# получали ведущие нули до той же длины.
LEVEL4_WIDTH = 6

MATERIAL_TYPES = {

    'book': {
        'label': 'Книга',
        'type_code': 'КН',
        'creator_types': ['author'],
        'level3_label': 'Том:',
        'level4_label': 'Страница:',
        'level4_kind': 'number',
        'source_fields': [
            ('publisher', 'Издательство:', 'publisher'),
            ('place', 'Место издания:', 'place'),
        ],
    },

    'audioRecording': {
        'label': 'Аудиозапись',
        'type_code': 'АУ',
        'creator_types': ['performer', 'composer', 'contributor'],
        'level3_label': 'Часть/эпизод:',
        'level4_label': (
            'Тайм-код (например 12:34 или 1:02:03):'
        ),
        'level4_kind': 'timecode',
        'source_fields': [
            ('platform', 'Платформа/сервис:', 'label'),
            ('channel', 'Канал/источник публикации:', None),
        ],
    },

    'videoRecording': {
        'label': 'Видеозапись',
        'type_code': 'ВИ',
        'creator_types': ['director', 'contributor'],
        'level3_label': 'Часть/эпизод:',
        'level4_label': (
            'Тайм-код (например 12:34 или 1:02:03):'
        ),
        'level4_kind': 'timecode',
        'source_fields': [
            ('platform', 'Платформа:', 'studio'),
            ('channel', 'Канал:', None),
        ],
    },

    'webpage': {
        'label': 'Веб-страница',
        'type_code': 'ВБ',
        'creator_types': ['author'],
        'level3_label': None,
        'level4_label': 'Номер раздела по оглавлению:',
        'level4_kind': 'number',
        'source_fields': [
            ('site', 'Сайт:', 'websiteTitle'),
            ('access_date', 'Дата обращения:', 'accessDate'),
        ],
    },

    'journalArticle': {
        'label': 'Статья',
        'type_code': 'СТ',
        'creator_types': ['author'],
        'level3_label': 'Том:',
        'level4_label': 'Страница:',
        'level4_kind': 'number',
        'source_fields': [
            ('journal', 'Журнал:', 'publicationTitle'),
            ('issue', 'Номер выпуска:', 'issue'),
        ],
    },

    'encyclopediaArticle': {
        'label': 'Энциклопедическая статья',
        'type_code': 'ЭС',
        'creator_types': ['author'],
        'level3_label': None,
        'level4_label': None,
        'level4_kind': 'number',
        'source_fields': [
            ('encyclopedia', 'Энциклопедия:', 'encyclopediaTitle'),
        ],
    },

    'blogPost': {
        'label': 'Запись в блоге',
        'type_code': 'БЛ',
        'creator_types': ['author'],
        'level3_label': None,
        'level4_label': None,
        'level4_kind': 'number',
        'source_fields': [
            ('blog', 'Сайт:', 'blogTitle'),
        ],
    },

}

SORTABLE_INDEX_FIELDS = [
    "name",
    "author",
    "work",
    "item_type",
    "date",
    "place",
    "fragment",
]

INDEX_SORTABLE_FIELDS = {
    "name": "Имя",
    "author": "Автор",
    "work": "Произведение",
    "item_type": "Тип",
    "date": "Дата",
    "place": "Место",
    "fragment": "Фрагмент",
}

DEFAULT_SORT_SPEC = [
    ("author", False),
    ("work", False),
    ("date", False),
]

def _sort_value(value):
    if value is None:
        return ""

    return str(value).casefold()

def _validate_sort_spec(sort_spec):

    if not isinstance(sort_spec, (list, tuple)):
        raise ValueError(
            "sort_spec должен быть списком или кортежем"
        )

    seen = set()

    for item in sort_spec:

        if not isinstance(item, (list, tuple)):
            raise ValueError(
                "Элемент sort_spec должен быть парой "
                "(field, reverse)"
            )

        if len(item) != 2:
            raise ValueError(
                "Элемент sort_spec должен содержать "
                "поле и направление сортировки"
            )

        field, reverse = item

        if field not in SORTABLE_INDEX_FIELDS:
            raise ValueError(
                f"Поле не поддерживает сортировку: {field}"
            )

        if field in seen:
            raise ValueError(
                f"Поле указано несколько раз: {field}"
            )

        if reverse not in (False, True):
            raise ValueError(
                f"Некорректное направление сортировки "
                f"для поля {field}: {reverse}"
            )

        seen.add(field)

    return True

def _add_sort_field(sort_spec, field):

    _validate_sort_spec(sort_spec)

    if field not in SORTABLE_INDEX_FIELDS:
        raise ValueError(
            f"Поле не поддерживает сортировку: {field}"
        )

    if any(
        existing_field == field
        for existing_field, reverse in sort_spec
    ):
        raise ValueError(
            f"Поле уже присутствует в сортировке: {field}"
        )

    result = list(sort_spec)

    # Новое поле всегда добавляется с сортировкой по возрастанию.
    result.append(
        (field, False)
    )

    return result

def _remove_sort_field(sort_spec, field):

    _validate_sort_spec(sort_spec)

    if not any(
        existing_field == field
        for existing_field, reverse in sort_spec
    ):
        raise ValueError(
            f"Поле отсутствует в сортировке: {field}"
        )

    if len(sort_spec) == 1:
        raise ValueError(
            "Нельзя удалить последнее поле сортировки"
        )

    result = [
        (existing_field, reverse)
        for existing_field, reverse in sort_spec
        if existing_field != field
    ]

    return result

def _toggle_sort_direction(sort_spec, field):

    _validate_sort_spec(sort_spec)

    result = []

    found = False

    for existing_field, reverse in sort_spec:

        if existing_field == field:

            result.append(
                (
                    existing_field,
                    not reverse,
                )
            )

            found = True

        else:

            result.append(
                (
                    existing_field,
                    reverse,
                )
            )

    if not found:
        raise ValueError(
            f"Поле отсутствует в сортировке: {field}"
        )

    return result

def _move_sort_field(sort_spec, field, new_position):

    _validate_sort_spec(sort_spec)

    if not any(
        existing_field == field
        for existing_field, reverse in sort_spec
    ):
        raise ValueError(
            f"Поле отсутствует в сортировке: {field}"
        )

    if not isinstance(new_position, int):
        raise ValueError(
            "Позиция должна быть целым числом"
        )

    if new_position < 0 or new_position >= len(sort_spec):
        raise ValueError(
            f"Недопустимая позиция: {new_position}"
        )

    result = list(sort_spec)

    current_position = next(
        index
        for index, (existing_field, reverse)
        in enumerate(result)
        if existing_field == field
    )

    item = result.pop(current_position)

    result.insert(
        new_position,
        item,
    )

    return result

# # QUESTION !
# def _show_material_index_window(
    # parent,
    # records,
    # sort_spec,
# ):
    # """
    # Показывает Material Index в отдельном GTK-окне.

    # На этом этапе:
    # - данные берутся из Material Index Model;
    # - записи предварительно сортируются по sort_spec;
    # - отображается Gtk.TreeView;
    # - отдельные ячейки пока не являются ссылками.
    # """

    # records = _sort_material_index_records(
        # records,
        # sort_spec,
    # )

    # model = Gtk.ListStore(
        # str,  # 0 - Автор
        # str,  # 1 - Произведение
        # str,  # 2 - Тип
        # str,  # 3 - Дата
        # str,  # 4 - Место
        # str,  # 5 - Фрагмент
        # object,  # 6 - полная запись Material Index
    # )


    def on_sort_button_clicked(button):

        new_sort_spec = _show_sort_spec_dialog(
            window,
            sort_spec,
        )

        if new_sort_spec is None:
            return

        _save_index_sort_spec(
            new_sort_spec
        )

        sort_spec[:] = new_sort_spec

        sorted_records = _sort_material_index_records(
            records,
            sort_spec,
        )

        fill_model(sorted_records)

    def on_button_press(
        treeview,
        event,
    ):

        if (
            event.button == 1
            and event.type == Gdk.EventType._2BUTTON_PRESS
        ):

            model = treeview.get_model()

            tree_path, column, _, _ = (
                treeview.get_path_at_pos(
                    int(event.x),
                    int(event.y),
                )
            )

            if tree_path is None:
                return False

            iterator = model.get_iter(
                tree_path
            )

            if iterator is None:
                return False

            record = model.get_value(
                iterator,
                6,
            )

            if record is None:
                return False

            material_name = record['name']

            try:

                material_path = _material_zim_path(
                    material_name
                )

                parent.open_page(
                    material_path
                )

            except Exception as error:

                print(
                    f'Не удалось открыть Material:\n'
                    f'{material_name}\n\n'
                    f'{error}',
                    flush=True,
                )

        return False

    treeview.connect(
        'button-press-event',
        on_button_press,
    )

    treeview.set_headers_visible(
        True
    )

    columns = [
        ('Автор', 0),
        ('Произведение', 1),
        ('Тип', 2),
        ('Дата', 3),
        ('Место', 4),
        ('Фрагмент', 5),
    ]

    for title, column_id in columns:

        renderer = Gtk.CellRendererText()

        renderer.set_property(
            'ellipsize',
            Pango.EllipsizeMode.END,
        )

        column = Gtk.TreeViewColumn(
            title,
            renderer,
            text=column_id,
        )

        column.set_resizable(
            True
        )

        column.set_sort_column_id(
            column_id
        )

        treeview.append_column(
            column
        )

    sort_button = Gtk.Button(
        label='Сортировка…'
    )

    sort_button.connect(
        'clicked',
        on_sort_button_clicked,
    )

    scrolled_window = Gtk.ScrolledWindow()

    scrolled_window.set_policy(
        Gtk.PolicyType.AUTOMATIC,
        Gtk.PolicyType.AUTOMATIC,
    )

    scrolled_window.add(
        treeview
    )

    window = Gtk.Window(
        title='Material Index'
    )

    window.set_default_size(
        1100,
        500,
    )

    window.set_transient_for(
        parent
    )

    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=6,
    )

    box.pack_start(
        sort_button,
        False,
        False,
        0,
    )

    box.pack_start(
        scrolled_window,
        True,
        True,
        0,
    )

    window.add(
        box
    )

    window.show_all()

    return window

def _show_sort_spec_dialog(parent, sort_spec):
    dialog = Gtk.Dialog(
        title="Сортировка Index",
        transient_for=parent,
        modal=True,
    )

    dialog.add_button(
        "По умолчанию",
        Gtk.ResponseType.APPLY,
    )

    dialog.add_button(
        "Отмена",
        Gtk.ResponseType.CANCEL,
    )

    dialog.add_button(
        "Применить",
        Gtk.ResponseType.OK,
    )

    content = dialog.get_content_area()

    main_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
    )

    main_box.set_border_width(12)

    content.add(main_box)

    label = Gtk.Label(
        label="Сортировать по:"
    )

    label.set_xalign(0)

    main_box.pack_start(
        label,
        False,
        False,
        0,
    )

    rows_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=4,
    )

    main_box.pack_start(
        rows_box,
        False,
        False,
        0,
    )

    add_box = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=6,
    )

    main_box.pack_start(
        add_box,
        False,
        False,
        8,
    )

    add_label = Gtk.Label(
        label="Добавить поле:"
    )

    add_box.pack_start(
        add_label,
        False,
        False,
        0,
    )

    combo = Gtk.ComboBoxText()

    add_box.pack_start(
        combo,
        True,
        True,
        0,
    )

    add_button = Gtk.Button(
        label="Добавить"
    )

    add_box.pack_start(
        add_button,
        False,
        False,
        0,
    )

    current_sort_spec = list(sort_spec)

    def refresh():

        for child in rows_box.get_children():
            rows_box.remove(child)

        used_fields = {
            field
            for field, reverse in current_sort_spec
        }

        for position, (field, reverse) in enumerate(
            current_sort_spec
        ):

            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
            )

            name_label = Gtk.Label(
                label=INDEX_SORTABLE_FIELDS[field]
            )

            name_label.set_xalign(0)

            row.pack_start(
                name_label,
                True,
                True,
                0,
            )

            direction_button = Gtk.Button(
                label="↓" if reverse else "↑"
            )

            direction_button.set_tooltip_text(
                "Изменить направление сортировки"
            )

            row.pack_start(
                direction_button,
                False,
                False,
                0,
            )

            up_button = Gtk.Button(
                label="▲"
            )

            up_button.set_tooltip_text(
                "Повысить приоритет"
            )

            row.pack_start(
                up_button,
                False,
                False,
                0,
            )

            down_button = Gtk.Button(
                label="▼"
            )

            down_button.set_tooltip_text(
                "Понизить приоритет"
            )

            row.pack_start(
                down_button,
                False,
                False,
                0,
            )

            remove_button = Gtk.Button(
                label="×"
            )

            remove_button.set_tooltip_text(
                "Удалить поле"
            )

            row.pack_start(
                remove_button,
                False,
                False,
                0,
            )

            def toggle_direction(
                button,
                field=field,
            ):
                nonlocal current_sort_spec

                current_sort_spec = (
                    _toggle_sort_direction(
                        current_sort_spec,
                        field,
                    )
                )

                refresh()

            def move_up(
                button,
                field=field,
                position=position,
            ):
                nonlocal current_sort_spec

                if position == 0:
                    return

                current_sort_spec = (
                    _move_sort_field(
                        current_sort_spec,
                        field,
                        position - 1,
                    )
                )

                refresh()

            def move_down(
                button,
                field=field,
                position=position,
            ):
                nonlocal current_sort_spec

                if position >= len(current_sort_spec) - 1:
                    return

                current_sort_spec = (
                    _move_sort_field(
                        current_sort_spec,
                        field,
                        position + 1,
                    )
                )

                refresh()

            def remove_field(
                button,
                field=field,
            ):
                nonlocal current_sort_spec

                if len(current_sort_spec) == 1:
                    return

                current_sort_spec = (
                    _remove_sort_field(
                        current_sort_spec,
                        field,
                    )
                )

                refresh()

            direction_button.connect(
                "clicked",
                toggle_direction,
            )

            up_button.connect(
                "clicked",
                move_up,
            )

            down_button.connect(
                "clicked",
                move_down,
            )

            remove_button.set_sensitive(
                len(current_sort_spec) > 1
            )

            remove_button.connect(
                "clicked",
                remove_field,
            )

            up_button.set_sensitive(
                position > 0
            )

            down_button.set_sensitive(
                position < len(current_sort_spec) - 1
            )

            rows_box.pack_start(
                row,
                False,
                False,
                0,
            )

        combo.remove_all()

        first_field = None

        for field in SORTABLE_INDEX_FIELDS:

            if field not in used_fields:

                if first_field is None:
                    first_field = field

                combo.append(
                    field,
                    INDEX_SORTABLE_FIELDS[field],
                )

        if first_field is not None:

            combo.set_active_id(
                first_field
            )

        combo.set_sensitive(
            bool(first_field)
        )

        add_button.set_sensitive(
            bool(first_field)
        )

        rows_box.show_all()
        combo.show_all()

    def add_field(button):

        nonlocal current_sort_spec

        field = combo.get_active_id()

        if not field:
            return

        current_sort_spec = _add_sort_field(
            current_sort_spec,
            field,
        )

        refresh()

    add_button.connect(
        "clicked",
        add_field,
    )

    refresh()

    dialog.show_all()

    while True:

        response = dialog.run()

        if response == Gtk.ResponseType.APPLY:

            current_sort_spec = list(
                DEFAULT_SORT_SPEC
            )

            refresh()

            continue

        if response == Gtk.ResponseType.OK:

            result = current_sort_spec

        else:

            result = list(sort_spec)

        break

    dialog.destroy()

    return result

def _sort_material_index_records(records, sort_spec):

    _validate_sort_spec(sort_spec)

    result = list(records)

    for field, reverse in reversed(sort_spec):
        result.sort(
            key=lambda record: _sort_value(record.get(field)),
            reverse=reverse,
        )

    return result

# def _build_material_index_text(index_records):
    # """
    # Формирует готовый Zim Wiki-текст Material Index.

    # index_records должны быть уже отсортированы.
    # """

    # lines = [
        # '====== Material Index ======',
        # '',
    # ]

    # for record in index_records:

        # lines.append(
            # '| '
            # f'{_material_index_link(record, "author")} | '
            # f'{_material_index_link(record, "work")} | '
            # f'{record["item_type"]} | '
            # f'{record["date"]} | '
            # f'{record["place"]} | '
            # f'{_material_index_link(record, "material")} |'
        # )

    # return '\n'.join(lines)

def _extract_creator_name(creator):

    name = creator.get('name', '')

    if name:
        return name

    first = creator.get('firstName', '').strip()
    last = creator.get('lastName', '').strip()

    if not first:
        return last

    if not last:
        return first

    initials = ''.join(
        part[0]
        for part in first.split()
        if part
    )

    initials = ''.join(
        f'{letter}.'
        for letter in initials
    )

    return f'{last} {initials}'.strip()


def _extract_primary_creator(creators, preferred_types):
    """
    Возвращает имя первого creator'а, чей creatorType входит
    в preferred_types (в порядке приоритета). Если такого нет —
    возвращает первого creator'а из списка вообще, поскольку
    транслейторы Zotero не всегда проставляют ожидаемый
    creatorType (особенно для сохранений через коннектор).
    """

    for creator_type in preferred_types:

        for creator in creators:

            if creator.get('creatorType') == creator_type:

                name = _extract_creator_name(creator)

                if name:
                    return name

    for creator in creators:

        name = _extract_creator_name(creator)

        if name:
            return name

    return ''

# =============================================================
# Prefix Model
# =============================================================

def _author_code_from_creator(creator):
    """
    Авторский код:

        первая буква фамилии + первая буква имени.

    Если имя невозможно надёжно разобрать автоматически,
    возвращается '00'.
    """

    if not creator:
        return '00'

    # Если Zotero хранит creator только как name,
    # автоматически разбирать его не будем.
    if creator.get('name'):
        return '00'

    first_name = (
        creator.get('firstName') or ''
    ).strip()

    last_name = (
        creator.get('lastName') or ''
    ).strip()

    if not first_name or not last_name:
        return '00'

    last_initial = next(
        (
            ch
            for ch in last_name
            if ch.isalpha()
        ),
        None,
    )

    first_initial = next(
        (
            ch
            for ch in first_name
            if ch.isalpha()
        ),
        None,
    )

    if not last_initial or not first_initial:
        return '00'

    return (
        last_initial.upper()
        + first_initial.upper()
    )

def _title_code(title):
    """
    Первоначальное механическое сокращение названия.

    Берём первые две буквы, игнорируя пробелы
    и небуквенные символы.
    """

    if not title:
        return '00'

    letters = [
        ch
        for ch in title
        if ch.isalpha()
    ]

    if not letters:
        return '00'

    if len(letters) == 1:
        return letters[0].upper()

    return (
        letters[0].upper()
        + letters[1].upper()
    )

def _generate_prefix(item_data):
    """
    Генерирует автоматическое предложение Prefix:

        AUTHOR-TITLE

    Short Title используется, если он задан.
    Иначе используется Title.
    """

    creators = item_data.get(
        'creators',
        [],
    )

    if creators:
        author_code = _author_code_from_creator(
            creators[0]
        )
    else:
        author_code = '00'

    short_title = (
        item_data.get('shortTitle') or ''
    ).strip()

    title = (
        short_title
        or item_data.get('title', '')
        or ''
    ).strip()

    title_code = _title_code(title)

    return f'{author_code}-{title_code}'

def _load_prefix_overrides():
    """
    Загружает пользовательские переопределения Prefix.

    Отсутствие файла не является ошибкой.
    """

    if not PREFIX_OVERRIDES_FILE.is_file():
        return {}

    try:

        with open(
            PREFIX_OVERRIDES_FILE,
            'r',
            encoding='utf-8',
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        return data

    except Exception as error:

        print(
            'WARNING: cannot read Prefix overrides:',
            error,
        )

        return {}

def _save_prefix_override(
    zotero_key,
    prefix,
):
    """
    Сохраняет пользовательский Prefix
    для данного Zotero Key.
    """

    if not zotero_key or not prefix:
        return

    overrides = _load_prefix_overrides()

    parts = prefix.split('-', 1)

    if len(parts) != 2:
        return

    author_code, title_code = parts

    overrides[zotero_key] = {
        'author': author_code,
        'title': title_code,
    }

    PREFIX_OVERRIDES_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        PREFIX_OVERRIDES_FILE,
        'w',
        encoding='utf-8',
    ) as f:
        json.dump(
            overrides,
            f,
            ensure_ascii=False,
            indent=2,
        )

def _apply_prefix_override(
    item_data,
    generated_prefix,
):
    """
    Применяет override для данного Zotero Key.

    Формат:

        {
            "Y4TJ2BWR": {
                "title": "КЛ"
            }
        }
    """

    key = item_data.get('key', '')

    if not key:
        return generated_prefix

    overrides = _load_prefix_overrides()

    override = overrides.get(key)

    if not isinstance(override, dict):
        return generated_prefix

    parts = generated_prefix.split('-', 1)

    if len(parts) != 2:
        return generated_prefix

    author_code, title_code = parts

    override_author = (
        override.get('author') or ''
    ).strip()

    override_title = (
        override.get('title') or ''
    ).strip()

    if override_author:
        author_code = override_author

    if override_title:
        title_code = override_title

    return f'{author_code}-{title_code}'


def _get_prefix(item_data):
    """
    Возвращает окончательный Prefix:

        автоматическая генерация
            +
        пользовательский override.
    """

    generated_prefix = _generate_prefix(
        item_data
    )

    return _apply_prefix_override(
        item_data,
        generated_prefix,
    )

# # QUESTION !
# def _find_prefix_conflicts(
    # notebook,
    # parent_path,
    # prefix,
# ):
    # """
    # Ищет непосредственные дочерние страницы текущего
    # раздела с таким же Prefix.

    # Сравниваются только первые два компонента ID:

        # PREFIX-TYPE-LEVEL3-LEVEL4-NUMBER
        # ^^^^^^

    # Технические суффиксы автоматически НЕ создаются.
    # """

    # conflicts = []

    # for record in notebook.pages.list_pages(
        # parent_path
    # ):

        # child_name = record.name

        # basename = child_name.rsplit(
            # ':',
            # 1
        # )[-1]

        # parts = basename.split('-')

        # if len(parts) < 5:
            # continue

        # existing_prefix = (
            # parts[0]
            # + '-'
            # + parts[1]
        # )

        # if existing_prefix == prefix:
            # conflicts.append(
                # child_name
            # )

    # return conflicts

def _digits_only(text):

    return ''.join(
        ch for ch in (text or '')
        if ch.isdigit()
    )

def _normalize_level3_id(raw):
    """
    Нормализует уровень 3 (том/часть/эпизод) в 2-значный код.
    Пустое значение — нейтральный заполнитель '00'.
    """

    digits = _digits_only(raw)

    if not digits:
        return '00'

    return digits.zfill(2)

def _normalize_number_field(raw, width):

    digits = _digits_only(raw)

    if not digits:
        digits = '0'

    return digits.zfill(width)

def _normalize_timecode(raw):
    """
    Нормализует тайм-код вида 'ЧЧ:ММ:СС', 'ММ:СС' или 'СС'
    в 6-значную строку ЧЧММСС для использования в ID.
    """

    raw = (raw or '').strip()

    if not raw:
        return '000000'

    parts = [
        part.strip()
        for part in raw.split(':')
        if part.strip() != ''
    ]

    try:
        numbers = [int(part) for part in parts]

    except ValueError:

        digits = _digits_only(raw)

        return (digits or '0').zfill(6)[-6:]

    if not numbers:
        return '000000'

    if len(numbers) == 1:
        hours, minutes, seconds = 0, 0, numbers[0]

    elif len(numbers) == 2:
        hours, minutes, seconds = 0, numbers[0], numbers[1]

    else:
        hours, minutes, seconds = (
            numbers[-3], numbers[-2], numbers[-1]
        )

    return f'{hours:02d}{minutes:02d}{seconds:02d}'

class ZoteroCitationsPlugin(PluginClass):

    plugin_info = {
        'name': 'Zotero Citations',
        'description': 'Integration between Zim and Zotero for citations.',
        'author': 'Nikolay',
    }

class ZoteroMainWindowExtension(MainWindowExtension):

    def __init__(self, plugin, window):
        super().__init__(plugin, window)

        fragments_plugin = PluginManager().get('zotero_fragments')

        if fragments_plugin is None:
            self.fragments_plugin = None
            self.citations_uri = None
            return

        self.fragments_plugin = fragments_plugin
        self.citations_uri = (
            fragments_plugin.preferences['citations_notebook']
        )

        self.zotero_search.set_sensitive(
            self.window.notebook.uri == self.citations_uri
        )


    @action('Zotero Insert Fragment', menuhints='tools')
    def zotero_insert_fragment(self):

        try:
            notebook = _get_bibliography_notebook()

            materials = _find_material_pages(
                notebook
            )

            if not materials:
                self._show_error(
                    'Material pages не найдены.'
                )
                return

            dialog = Gtk.Dialog(
                title='Выберите Material',
                transient_for=self.window,
                modal=True,
            )

            dialog.set_default_size(
                900,
                500,
            )

            dialog.add_button(
                'Отмена',
                Gtk.ResponseType.CANCEL,
            )

            dialog.add_button(
                'Вставить',
                Gtk.ResponseType.OK,
            )

            content = dialog.get_content_area()

            content.set_border_width(10)

            scrolled = Gtk.ScrolledWindow()

            scrolled.set_policy(
                Gtk.PolicyType.AUTOMATIC,
                Gtk.PolicyType.AUTOMATIC,
            )

            scrolled.set_hexpand(True)
            scrolled.set_vexpand(True)

            listbox = Gtk.ListBox()

            listbox.set_selection_mode(
                Gtk.SelectionMode.SINGLE
            )

            listbox.set_hexpand(True)
            listbox.set_vexpand(True)

            for material_name in materials:

                row = Gtk.ListBoxRow()

                label = Gtk.Label(
                    label=material_name
                )

                label.set_xalign(0.0)

                label.set_hexpand(True)

                _apply_zim_text_font(
                    label
                )

                row.add(label)

                listbox.add(row)

            scrolled.add(listbox)

            content.add(scrolled)

            dialog.show_all()

            first_row = listbox.get_row_at_index(0)

            if first_row is not None:

                listbox.select_row(
                    first_row
                )

            response = dialog.run()

            selected_row = (
                listbox.get_selected_row()
            )

            selected = None

            if selected_row is not None:

                selected_label = (
                    selected_row.get_child()
                )

                selected = (
                    selected_label.get_text()
                )

            dialog.destroy()

            if response != Gtk.ResponseType.OK:
                return

            if not selected:
                return

            textview = (
                self.window.pageview.textview
            )

            _insert_material_fragment(
                notebook,
                selected,
                textview,
            )

        except Exception as error:

            self._show_error(
                f'Ошибка вставки Fragment:\n{error}'
            )


    @action('Zotero Search...', menuhints='tools')
    def zotero_search(self):

        if not ensure_zotero_running():
            self._show_error(
                'Не удалось запустить Zotero '
                'или дождаться его Local API.'
            )
            return

        api = ZoteroLocalAPI()

        dialog = Gtk.Dialog(
            title='Поиск в Zotero',
            transient_for=self.window,
            modal=True,
        )

        dialog.add_button(
            'Отмена',
            Gtk.ResponseType.CANCEL,
        )

        dialog.add_button(
            'Найти',
            Gtk.ResponseType.OK,
        )

        content = dialog.get_content_area()

        grid = Gtk.Grid()

        grid.set_column_spacing(12)
        grid.set_row_spacing(10)
        grid.set_border_width(12)


        # ---------------------------------------------------------
        # Автор
        # ---------------------------------------------------------

        author_label = Gtk.Label(
            label='Автор:'
        )

        author_label.set_halign(
            Gtk.Align.START
        )

        author_entry = Gtk.Entry()

        author_entry.set_hexpand(True)

        author_entry.set_placeholder_text(
            'Например: Маркс'
        )


        # ---------------------------------------------------------
        # Название
        # ---------------------------------------------------------

        title_label = Gtk.Label(
            label='Название:'
        )

        title_label.set_halign(
            Gtk.Align.START
        )

        title_entry = Gtk.Entry()

        title_entry.set_hexpand(True)

        title_entry.set_placeholder_text(
            'Например: Капитал'
        )


        # ---------------------------------------------------------
        # Ключевые слова
        # ---------------------------------------------------------

        tags_label = Gtk.Label(
            label='Теги Zotero:'
        )

        tags_label.set_halign(
            Gtk.Align.START
        )

        tags_entry = Gtk.Entry()

        tags_entry.set_hexpand(True)

        tags_entry.set_placeholder_text(
            'Начните вводить тег Zotero'
        )

        # ---------------------------------------------------------
        # Автодополнение тегов Zotero
        # ---------------------------------------------------------

        tag_store = Gtk.ListStore(str)

        try:
            tag_response = api.get(
                f"/users/{api.user_id}/tags",
                params={"limit": 10000},
            )

            for tag in tag_response.json():
                tag_name = tag.get("tag")
                if tag_name:
                    tag_store.append([tag_name])

            self._zotero_tags = [
                row[0]
                for row in tag_store
            ]

        except Exception:
            self._zotero_tags = []

        tag_completion = Gtk.EntryCompletion()
        tag_completion.set_model(tag_store)
        tag_completion.set_text_column(0)
        tag_completion.set_inline_completion(True)
        tag_completion.set_popup_completion(True)

        def match_tag(completion, key, tree_iter, data=None):
            text = tags_entry.get_text()

            # Берём только последний вводимый фрагмент.
            current = text.rsplit(' ', 1)[-1].strip()

            if not current:
                return True

            # Если пользователь вводит отрицательный тег,
            # убираем '-' только для поиска совпадения.
            if current.startswith('-'):
                current = current[1:]

            if not current:
                return True

            tag_name = tag_store[tree_iter][0]

            return tag_name.lower().startswith(
                current.lower()
            )

        tag_completion.set_match_func(match_tag)

        def on_tag_selected(completion, model, tree_iter):
            selected_tag = model[tree_iter][0]
            current_text = tags_entry.get_text()

            # Берём последний вводимый фрагмент.
            current_part = current_text.rsplit(' ', 1)[-1]

            # Запоминаем отрицательный тег.
            is_negative = current_part.startswith('-')

            if ' ' in current_text:
                prefix = current_text.rsplit(' ', 1)[0]

                if is_negative:
                    new_text = prefix + ' -' + selected_tag
                else:
                    new_text = prefix + ' ' + selected_tag
            else:
                if is_negative:
                    new_text = '-' + selected_tag
                else:
                    new_text = selected_tag

            tags_entry.set_text(new_text)
            tags_entry.set_position(-1)

            return True

        tag_completion.connect(
            'match-selected',
            on_tag_selected
        )

        tags_entry.set_completion(tag_completion)

        # ---------------------------------------------------------
        # Подсказка для ключевых слов
        # ---------------------------------------------------------

        tags_help = Gtk.Label(
            label=(
                'Несколько слов через пробел — AND. '
                'Для OR используйте ||. '
                'Для исключения — -слово.'
            )
        )

        tags_help.set_halign(
            Gtk.Align.START
        )

        tags_help.set_line_wrap(True)


        # ---------------------------------------------------------
        # Размещение
        # ---------------------------------------------------------

        grid.attach(
            author_label,
            0, 0, 1, 1
        )

        grid.attach(
            author_entry,
            1, 0, 1, 1
        )


        grid.attach(
            title_label,
            0, 1, 1, 1
        )

        grid.attach(
            title_entry,
            1, 1, 1, 1
        )


        grid.attach(
            tags_label,
            0, 2, 1, 1
        )

        grid.attach(
            tags_entry,
            1, 2, 1, 1
        )


        grid.attach(
            tags_help,
            1, 3, 1, 1
        )


        content.add(grid)


        author_entry.set_activates_default(True)
        title_entry.set_activates_default(True)
        tags_entry.set_activates_default(True)

        dialog.set_default_response(
            Gtk.ResponseType.OK
        )

        dialog.show_all()

        response = dialog.run()

        author = author_entry.get_text().strip()
        title = title_entry.get_text().strip()
        tags = tags_entry.get_text().strip()

        dialog.destroy()


        if response != Gtk.ResponseType.OK:
            return


        try:
            api = ZoteroLocalAPI()

            api.connect()

            results = self._search_items(
                api,
                author,
                title,
                tags,
            )

        except ZoteroAPIError as error:
            self._show_error(
                f'Ошибка Zotero:\n{error}'
            )
            return

        except Exception as error:
            self._show_error(
                f'Неожиданная ошибка:\n{error}'
            )
            return

        self._show_search_results(
            author,
            title,
            tags,
            results,
        )

# =============================================================
# Реальный поиск
# =============================================================

    def _search_items(
        self,
        api,
        author,
        title,
        tags,
    ):

        all_items = []

        start = 0

        tag_params = self._build_positive_tag_params(tags)
        negative_tags = self._extract_negative_tags(tags)

        while True:

            params = [
                ('q', ''),
                ('itemType', '-attachment'),
                ('limit', str(SEARCH_PAGE_SIZE)),
                ('start', str(start)),
            ]

            for tag in tag_params:

                if tag in negative_tags:
                    continue

                params.append(
                    ('tag', tag)
                )

            response = api.get(
                f'/users/{api.user_id}/items',
                params=params,
            )

            page = response.json()


            if not page:
                break


            all_items.extend(page)


            if len(page) < SEARCH_PAGE_SIZE:
                break


            start += SEARCH_PAGE_SIZE


        filtered_items = []


        author_query = author.casefold()
        title_query = title.casefold()


        for item in all_items:

            data = item.get(
                'data',
                {},
            )

            if data.get('parentItem'):
                continue

            # -----------------------------------------------------
            # Локальное исключение отрицательных тегов.
            # -----------------------------------------------------

            if negative_tags:

                item_tags = {
                    tag.get('tag', '').casefold()
                    for tag in data.get('tags', [])
                }

                if any(
                    tag.casefold() in item_tags
                    for tag in negative_tags
                ):
                    continue

            # -----------------------------------------------------
            # Автор
            # -----------------------------------------------------

            if author_query:

                creators = data.get(
                    'creators',
                    [],
                )

                creator_texts = []

                for creator in creators:

                    name = creator.get(
                        'name',
                        '',
                    )

                    first_name = creator.get(
                        'firstName',
                        '',
                    )

                    last_name = creator.get(
                        'lastName',
                        '',
                    )


                    if name:
                        creator_texts.append(name)

                    else:

                        full_name = (
                            f'{first_name} {last_name}'
                        ).strip()

                        if full_name:
                            creator_texts.append(
                                full_name
                            )


                creator_text = ' '.join(
                    creator_texts
                ).casefold()


                if author_query not in creator_text:
                    continue


            # -----------------------------------------------------
            # Название
            # -----------------------------------------------------

            if title_query:

                item_title = data.get(
                    'title',
                    '',
                )

                if title_query not in item_title.casefold():
                    continue


            filtered_items.append(item)


        return filtered_items


    # =============================================================
    # Извлечение отрицательных тегов
    # =============================================================

    def _extract_negative_tags(self, tags):

        if not tags:
            return []

        zotero_tags = getattr(
            self,
            '_zotero_tags',
            []
        )

        words = tags.split()

        result = []

        index = 0

        while index < len(words):

            word = words[index]

            # -----------------------------------------------------
            # Ищем слово с отрицательным префиксом '-'
            # -----------------------------------------------------

            if not word.startswith('-') or word == '-':
                index += 1
                continue

            first_word = word[1:]

            found_tag = None
            found_length = 0

            # -----------------------------------------------------
            # Пытаемся найти реальный тег Zotero.
            #
            # Это позволяет правильно обработать:
            #
            #   -марксизм
            #   -19 век
            #
            # -----------------------------------------------------

            for tag in zotero_tags:

                tag_words = tag.split()

                if not tag_words:
                    continue

                if (
                    tag_words[0].casefold()
                    != first_word.casefold()
                ):
                    continue

                length = len(tag_words)

                if (
                    words[index + 1:index + length]
                    == tag_words[1:]
                ):
                    if length > found_length:
                        found_tag = tag
                        found_length = length

            # -----------------------------------------------------
            # Если нашли существующий тег — используем его
            # настоящее написание из Zotero.
            # -----------------------------------------------------

            if found_tag is not None:

                result.append(found_tag)

                index += found_length

            else:

                # -------------------------------------------------
                # Если тега нет в списке Zotero, всё равно считаем
                # его отрицательным тегом.
                # -------------------------------------------------

                result.append(first_word)

                index += 1

        return result


# =============================================================
# Построение положительных tag-параметров для Zotero API
# =============================================================

    def _build_positive_tag_params(self, tags):

        if not tags:
            return []

        zotero_tags = getattr(
            self,
            '_zotero_tags',
            []
        )

        words = tags.split()

        result = []

        index = 0

        while index < len(words):

            word = words[index]

            # -----------------------------------------------------
            # Отрицательный тег — пропускаем.
            # -----------------------------------------------------

            if word.startswith('-') and word != '-':

                first_word = word[1:]

                found_length = 1

                for tag in zotero_tags:

                    tag_words = tag.split()

                    if not tag_words:
                        continue

                    if (
                        tag_words[0].casefold()
                        != first_word.casefold()
                    ):
                        continue

                    length = len(tag_words)

                    if (
                        words[index + 1:index + length]
                        == tag_words[1:]
                    ):
                        if length > found_length:
                            found_length = length

                index += found_length

                continue

            # -----------------------------------------------------
            # OR.
            # -----------------------------------------------------

            if (
                index + 1 < len(words)
                and words[index + 1] == '||'
            ):

                or_parts = []

                while index < len(words):

                    current = words[index]

                    if current.startswith('-'):
                        break

                    if current == '||':
                        index += 1
                        continue

                    found_tag = None
                    found_length = 0

                    for tag in zotero_tags:

                        tag_words = tag.split()

                        length = len(tag_words)

                        if (
                            words[index:index + length]
                            == tag_words
                        ):
                            if length > found_length:
                                found_tag = tag
                                found_length = length

                    if found_tag is not None:
                        or_parts.append(found_tag)
                        index += found_length
                    else:
                        or_parts.append(current)
                        index += 1

                    if (
                        index >= len(words)
                        or words[index].startswith('-')
                        or words[index] != '||'
                    ):
                        break

                if len(or_parts) >= 2:

                    result.append(
                        ' || '.join(or_parts)
                    )

                    continue

            # -----------------------------------------------------
            # Обычный положительный тег.
            # -----------------------------------------------------

            found_tag = None
            found_length = 0

            for tag in zotero_tags:

                tag_words = tag.split()

                length = len(tag_words)

                if (
                    words[index:index + length]
                    == tag_words
                ):
                    if length > found_length:
                        found_tag = tag
                        found_length = length

            if found_tag is not None:

                result.append(found_tag)

                index += found_length

            else:

                result.append(words[index])

                index += 1

        return result


    def _format_tag_query(self, tags):

        if not tags:
            return 'пусто'

        zotero_tags = getattr(
            self,
            '_zotero_tags',
            []
        )

        words = tags.split()

        groups = []
        current_group = []

        index = 0

        while index < len(words):

            word = words[index]

            # -----------------------------------------------------
            # OR — начинаем следующую часть выражения.
            # -----------------------------------------------------

            if word == '||':

                if current_group:
                    groups.append(current_group)
                    current_group = []

                index += 1
                continue

            # -----------------------------------------------------
            # Определяем отрицательный тег.
            # -----------------------------------------------------

            negative = (
                word.startswith('-')
                and word != '-'
            )

            first_word = (
                word[1:]
                if negative
                else word
            )

            # -----------------------------------------------------
            # Ищем самый длинный совпадающий Zotero-тег.
            # -----------------------------------------------------

            found_tag = None
            found_length = 0

            for tag in zotero_tags:

                tag_words = tag.split()

                if not tag_words:
                    continue

                if (
                    tag_words[0].casefold()
                    != first_word.casefold()
                ):
                    continue

                length = len(tag_words)

                if (
                    words[index + 1:index + length]
                    == tag_words[1:]
                    and length > found_length
                ):
                    found_tag = tag
                    found_length = length

            if found_tag is None:

                found_tag = first_word
                found_length = 1

            # -----------------------------------------------------
            # Формируем элемент выражения.
            # -----------------------------------------------------

            if negative:

                current_group.append(
                    f'NOT ({found_tag})'
                )

            else:

                current_group.append(
                    f'({found_tag})'
                )

            index += found_length

        if current_group:
            groups.append(current_group)

        # ---------------------------------------------------------
        # Формируем группы AND.
        # ---------------------------------------------------------

        group_texts = []

        for group in groups:

            if not group:
                continue

            if len(group) == 1:

                group_texts.append(
                    group[0]
                )

            else:

                group_texts.append(
                    ' AND '.join(group)
                )

        # ---------------------------------------------------------
        # OR между группами.
        # ---------------------------------------------------------

        if len(group_texts) == 1:

            return group_texts[0]

        return ' OR '.join(group_texts)


# =============================================================
# Результаты поиска
# =============================================================

    def _show_search_results(
        self,
        author,
        title,
        tags,
        results,
    ):

        items = []


        for item in results:

            data = item.get(
                'data',
                {},
            )


            item_title = data.get(
                'title',
                '(без названия)',
            )


            item_type = data.get(
                'itemType',
                '',
            )


            key = data.get(
                'key',
                '',
            )


            creators = data.get(
                'creators',
                [],
            )


            creator_names = []


            for creator in creators:

                name = creator.get(
                    'name',
                    '',
                )


                if name:
                    creator_names.append(name)
                    continue


                first_name = creator.get(
                    'firstName',
                    '',
                )

                last_name = creator.get(
                    'lastName',
                    '',
                )


                full_name = (
                    f'{last_name} {first_name}'
                ).strip()


                if full_name:
                    creator_names.append(
                        full_name
                    )


            item_author = ', '.join(
                creator_names
            )


            items.append({
                'title': item_title,
                'author': item_author,
                'item_type': item_type,
                'key': key,
            })


        # ---------------------------------------------------------
        # Строка фактического запроса
        # ---------------------------------------------------------

        query_parts = []

        query_parts.append(
            f'Автор = "{author if author else "пусто"}"'
        )

        query_parts.append(
            f'Название = "{title if title else "пусто"}"'
        )

        display_tags = self._format_tag_query(
            tags
        )

        query_parts.append(
            f'Теги = "{display_tags}"'
        )


        query_text = (
            'Поиск: '
            + '; '.join(query_parts)
        )


        # ---------------------------------------------------------
        # Диалог результатов
        # ---------------------------------------------------------

        dialog = Gtk.Dialog(
            title='Результаты поиска в Zotero',
            transient_for=self.window,
            modal=True,
        )


        dialog.add_button(
            'Отмена',
            Gtk.ResponseType.CANCEL,
        )


        open_button = dialog.add_button(
            'Открыть в Zotero',
            Gtk.ResponseType.OK,
        )

        create_button = dialog.add_button(
            'Создать материал',
            Gtk.ResponseType.APPLY
        )

        content = dialog.get_content_area()

        content.set_border_width(10)


        # ---------------------------------------------------------
        # Информация о запросе
        # ---------------------------------------------------------

        query_label = Gtk.Label(
            label=query_text
        )


        query_label.set_halign(
            Gtk.Align.START
        )


        query_label.set_line_wrap(True)


        content.pack_start(
            query_label,
            False,
            False,
            5,
        )


        count_label = Gtk.Label(
            label=f'Найдено: {len(items)}'
        )


        count_label.set_halign(
            Gtk.Align.START
        )


        content.pack_start(
            count_label,
            False,
            False,
            5,
        )


        # ---------------------------------------------------------
        # Список результатов
        # ---------------------------------------------------------

        store = Gtk.ListStore(
            str,
            str,
            str,
            str,
        )


        tree = Gtk.TreeView(
            model=store
        )


        tree.set_headers_visible(True)


        renderer_title = Gtk.CellRendererText()


        column_title = Gtk.TreeViewColumn(
            'Название',
            renderer_title,
            text=0,
        )


        column_title.set_expand(True)
        column_title.set_sort_column_id(0)

        tree.append_column(
            column_title
        )


        renderer_author = Gtk.CellRendererText()

        column_author = Gtk.TreeViewColumn(
            'Автор',
            renderer_author,
            text=1,
        )


        column_author.set_expand(True)
        column_author.set_sort_column_id(1)

        tree.append_column(
            column_author
        )


        renderer_type = Gtk.CellRendererText()


        column_type = Gtk.TreeViewColumn(
            'Тип',
            renderer_type,
            text=2,
        )

        column_type.set_sort_column_id(2)

        tree.append_column(
            column_type
        )


        renderer_key = Gtk.CellRendererText()


        column_key = Gtk.TreeViewColumn(
            'Key',
            renderer_key,
            text=3,
        )

        column_key.set_sort_column_id(3)

        tree.append_column(
            column_key
        )


        selection = tree.get_selection()


        # ---------------------------------------------------------
        # Обновление списка текущей страницы
        # ---------------------------------------------------------

        current_page = 0


        def update_page():

            store.clear()


            start_index = (
                current_page
                * SEARCH_PAGE_SIZE
            )


            end_index = min(
                start_index + SEARCH_PAGE_SIZE,
                len(items),
            )


            for item in items[
                start_index:end_index
            ]:

                store.append([
                    item['title'],
                    item['author'],
                    item['item_type'],
                    item['key'],
                ])


            page_count = max(
                1,
                (
                    len(items)
                    + SEARCH_PAGE_SIZE
                    - 1
                )
                // SEARCH_PAGE_SIZE,
            )


            page_label.set_text(
                f'{current_page + 1} / {page_count}'
            )


            previous_button.set_sensitive(
                current_page > 0
            )


            next_button.set_sensitive(
                current_page < page_count - 1
            )


            selection.unselect_all()


            if len(store) > 0:

                first_iterator = store.get_iter_first()


                if first_iterator is not None:

                    selection.select_iter(
                        first_iterator
                    )


            update_open_button()


        # ---------------------------------------------------------
        # Состояние кнопки Open
        # ---------------------------------------------------------

        def update_open_button(*args):

            model, iterator = (
                selection.get_selected()
            )


            open_button.set_sensitive(
                iterator is not None
            )


        selection.connect(
            'changed',
            update_open_button,
        )


        # ---------------------------------------------------------
        # Двойной клик
        # ---------------------------------------------------------

        def on_row_activated(
            tree_view,
            path,
            column,
        ):

            model = tree_view.get_model()


            iterator = model.get_iter(path)


            key = model.get_value(
                iterator,
                3,
            )


            if key:

                dialog.response(
                    Gtk.ResponseType.OK
                )


        tree.connect(
            'row-activated',
            on_row_activated,
        )


        # ---------------------------------------------------------
        # Прокрутка
        # ---------------------------------------------------------

        scrolled = Gtk.ScrolledWindow()


        scrolled.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )


        scrolled.set_min_content_width(
            900
        )


        scrolled.set_min_content_height(
            400
        )


        scrolled.add(tree)


        content.pack_start(
            scrolled,
            True,
            True,
            5,
        )


        # ---------------------------------------------------------
        # Пагинация
        # ---------------------------------------------------------

        navigation = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
        )


        previous_button = Gtk.Button(
            label='←'
        )


        page_label = Gtk.Label(
            label='1 / 1'
        )


        next_button = Gtk.Button(
            label='→'
        )


        navigation.pack_start(
            previous_button,
            False,
            False,
            0,
        )


        navigation.pack_start(
            page_label,
            False,
            False,
            0,
        )


        navigation.pack_start(
            next_button,
            False,
            False,
            0,
        )


        content.pack_start(
            navigation,
            False,
            False,
            5,
        )


        # ---------------------------------------------------------
        # Переключение страниц
        # ---------------------------------------------------------

        def previous_page(button):

            nonlocal current_page


            if current_page > 0:

                current_page -= 1

                update_page()


        def next_page(button):

            nonlocal current_page


            page_count = max(
                1,
                (
                    len(items)
                    + SEARCH_PAGE_SIZE
                    - 1
                )
                // SEARCH_PAGE_SIZE,
            )


            if current_page < page_count - 1:

                current_page += 1

                update_page()


        previous_button.connect(
            'clicked',
            previous_page,
        )


        next_button.connect(
            'clicked',
            next_page,
        )


        dialog.show_all()


        # ---------------------------------------------------------
        # Первоначальная страница.
        #
        # Gtk может выбрать первую строку автоматически,
        # но сигнал changed при этом не обязан сработать.
        # Поэтому явно синхронизируем состояние.
        # ---------------------------------------------------------

        update_page()

        response = dialog.run()


        selected_key = None


        if response in (
            Gtk.ResponseType.OK,
            Gtk.ResponseType.APPLY,
        ):

            model, iterator = (
                selection.get_selected()
            )


            if iterator is not None:

                selected_key = model.get_value(
                    iterator,
                    3,
                )


        dialog.destroy()


        if not selected_key:
            return


        # ---------------------------------------------------------
        # Создание материала
        # ---------------------------------------------------------

        if response == Gtk.ResponseType.APPLY:

            try:
                api = ZoteroLocalAPI()
                api.connect()

                item_data = api.get_item_data(
                    selected_key
                )

                item_type = item_data.get('itemType')

                if item_type not in MATERIAL_TYPES:

                    supported = ', '.join(
                        config['label']
                        for config in MATERIAL_TYPES.values()
                    )

                    self._show_error(
                        'Создание материала не реализовано '
                        'для этого типа Zotero.\n'
                        f'Поддерживаются: {supported}.'
                    )

                    return


                self._create_material(
                    item_data
                )

            except ZoteroAPIError as error:

                self._show_error(
                    f'Ошибка получения данных Item:\n{error}'
                )

            except Exception as error:

                self._show_error(
                    f'Неожиданная ошибка:\n{error}'
                )

            return


        # ---------------------------------------------------------
        # Открытие Item в Zotero
        # ---------------------------------------------------------

        if response == Gtk.ResponseType.OK:

            if not open_item_in_zotero(
                selected_key
            ):

                self._show_error(
                    'Не удалось открыть Item в Zotero.'
                )

                return


    def _create_material(self, item_data):
        """
        Создаёт страницу материала в текущем блокноте Zim
        для произвольного поддерживаемого типа Zotero
        (см. MATERIAL_TYPES).

        ID страницы строится по общей модели уровней:

            {префикс}-{код типа}-{уровень 3}-{уровень 4}-{номер}

        где код типа, формат уровня 3 (том/часть/эпизод) и уровня 4
        (страница/тайм-код/раздел) определяются конфигурацией типа.
        Номер материала определяется автоматически: берётся
        максимальный номер среди непосредственных дочерних страниц
        текущей страницы + 1.
        """

        item_type = item_data.get('itemType', '')

        config = MATERIAL_TYPES.get(item_type)

        if config is None:

            self._show_error(
                'Создание материала не реализовано '
                'для этого типа Zotero.'
            )

            return

        dialog = Gtk.Dialog(
            title=f"Создать материал — {config['label']}",
            transient_for=self.window,
            modal=True,
        )

        dialog.set_default_size(800, 700)

        dialog.add_button('Отмена', Gtk.ResponseType.CANCEL)
        dialog.add_button('Создать', Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_border_width(10)

        grid = Gtk.Grid()
        grid.set_row_spacing(6)
        grid.set_column_spacing(10)
        content.pack_start(grid, False, False, 0)

        next_row = [0]

        def add_entry(label_text, value=''):

            row = next_row[0]
            next_row[0] += 1

            label = Gtk.Label(label=label_text)
            label.set_halign(Gtk.Align.START)

            entry = Gtk.Entry()
            entry.set_text(value)

            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            entry.set_hexpand(True)

            return entry

        # ---------------------------------------------------------
        # Общие библиографические данные
        # ---------------------------------------------------------

        creators = item_data.get('creators', [])

        author = _extract_primary_creator(
            creators,
            config['creator_types'],
        )

        title = item_data.get('title', '')
        date = item_data.get('date', '')
        zotero_key = item_data.get('key', '')

        author_entry = add_entry('Автор:', author)
        title_entry = add_entry('Название:', title)
        type_entry = add_entry('Тип:', config['label'])
        date_entry = add_entry('Дата:', date)

        # ---------------------------------------------------------
        # Дополнительные поля источника (зависят от типа)
        # ---------------------------------------------------------

        source_entries = {}

        for key, label_text, zotero_field in config['source_fields']:

            initial = (
                item_data.get(zotero_field, '') or ''
                if zotero_field
                else ''
            )

            source_entries[key] = add_entry(label_text, initial)

# ---------------------------------------------------------
# Prefix
#
# Prefix автоматически предлагается на основании Zotero Item.
#
# Формат:
#
#     AUTHOR-TITLE
#
# Автор:
#     первая буква фамилии + первая буква имени.
#
# Название:
#     Short Title, если задан;
#     иначе Title.
#
# Пользовательский override имеет приоритет.
#
# Поле остаётся редактируемым пользователем.
# ---------------------------------------------------------

        generated_prefix = _get_prefix(
            item_data
        )

        prefix_entry = add_entry(
            'Префикс ID:',
            generated_prefix,
        )

        level3_entry = None

        if config['level3_label']:
            level3_entry = add_entry(config['level3_label'], '')

        level4_entry = None

        if config['level4_label']:
            if item_type == 'webpage':
                level4_initial = '1'
            else:
                level4_initial = ''

            level4_entry = add_entry(
                config['level4_label'],
                level4_initial,
            )

        # ---------------------------------------------------------
        # Место — произвольное количество уровней
        # ---------------------------------------------------------

        place_entries = []

        place_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )

        grid.attach(
            place_box,
            0,
            next_row[0],
            2,
            1,
        )

        next_row[0] += 1

        add_level_button = Gtk.Button(
            label='+ Добавить уровень'
        )

        add_level_button.set_halign(
            Gtk.Align.START
        )

        def add_place_level(value=''):

            level_number = len(place_entries) + 1

            level_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10,
            )

            label = Gtk.Label(
                label=f'Уровень {level_number}:'
            )

            label.set_halign(
                Gtk.Align.START
            )

            entry = Gtk.Entry()

            entry.set_text(
                value
            )

            level_box.pack_start(
                label,
                False,
                False,
                0,
            )

            level_box.pack_start(
                entry,
                True,
                True,
                0,
            )

            place_box.pack_start(
                level_box,
                False,
                False,
                0,
            )

            place_entries.append(
                entry
            )

            place_box.reorder_child(
                add_level_button,
                len(place_entries),
            )

            level_box.show_all()

        add_level_button.connect(
            'clicked',
            lambda button: add_place_level()
        )

        add_place_level()

        place_box.pack_start(
            add_level_button,
            False,
            False,
            0,
        )

        place_box.show_all()

        # ---------------------------------------------------------
        # Материал
        # ---------------------------------------------------------

        material_label = Gtk.Label(label='Материал:')
        material_label.set_halign(Gtk.Align.START)

        material_text = Gtk.TextView()

        material_text.set_wrap_mode(Gtk.WrapMode.WORD)
        material_text.set_size_request(700, 180)

        material_scroll = Gtk.ScrolledWindow()

        material_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )

        material_scroll.add(material_text)

        material_row = next_row[0]
        next_row[0] += 1

        grid.attach(material_label, 0, material_row, 1, 1)
        grid.attach(material_scroll, 1, material_row, 1, 1)

        # ---------------------------------------------------------
        # Комментарий
        # ---------------------------------------------------------

        comment_label = Gtk.Label(label='Комментарий:')
        comment_label.set_halign(Gtk.Align.START)

        comment_text = Gtk.TextView()

        comment_text.set_wrap_mode(Gtk.WrapMode.WORD)
        comment_text.set_size_request(700, 100)

        comment_scroll = Gtk.ScrolledWindow()

        comment_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )

        comment_scroll.add(comment_text)

        comment_row = next_row[0]
        next_row[0] += 1

        grid.attach(comment_label, 0, comment_row, 1, 1)
        grid.attach(comment_scroll, 1, comment_row, 1, 1)

        # ---------------------------------------------------------
        # Краткая информация об источнике
        # ---------------------------------------------------------

        source_label = Gtk.Label(
            label=f'Источник: {author} — {title}'
        )

        source_label.set_halign(Gtk.Align.START)

        source_row = next_row[0]
        next_row[0] += 1

        grid.attach(source_label, 0, source_row, 2, 1)

        dialog.show_all()

        response = dialog.run()

        if response != Gtk.ResponseType.OK:

            dialog.destroy()

            return

        # ---------------------------------------------------------
        # Получаем введённые данные
        # ---------------------------------------------------------

        author = author_entry.get_text().strip()
        title = title_entry.get_text().strip()
        item_type_label = type_entry.get_text().strip()
        date = date_entry.get_text().strip()

        source_values = {
            key: entry.get_text().strip()
            for key, entry in source_entries.items()
        }

        prefix = prefix_entry.get_text().strip()

        level3_raw = (
            level3_entry.get_text().strip()
            if level3_entry is not None
            else ''
        )

        level4_raw = (
            level4_entry.get_text().strip()
            if level4_entry is not None
            else ''
        )

        place_values = [
            entry.get_text().strip()
            for entry in place_entries
        ]

        def get_buffer_text(text_view):

            buffer = text_view.get_buffer()

            start, end = buffer.get_bounds()

            return buffer.get_text(
                start,
                end,
                False,
            ).strip()

        material = get_buffer_text(material_text)
        comment = get_buffer_text(comment_text)

        dialog.destroy()

        # ---------------------------------------------------------
        # Проверяем обязательные поля
        # ---------------------------------------------------------

        if not prefix or (
            config['level4_label']
            and not level4_raw
        ):
            self._show_error(
                'Необходимо заполнить:\n'
                '• Префикс ID'
                + (
                    f"\n• {config['level4_label']}"
                    if config['level4_label']
                    else ''
                )
            )

            return

        _save_prefix_override(
            zotero_key,
            prefix,
        )

        # ---------------------------------------------------------
        # Нормализуем компоненты ID
        # ---------------------------------------------------------

        type_code = config['type_code']

        level3_id = _normalize_level3_id(level3_raw)

        if config['level4_kind'] == 'timecode':
            level4_id = _normalize_timecode(level4_raw)
        else:
            level4_id = _normalize_number_field(
                level4_raw,
                LEVEL4_WIDTH,
            )

        # ---------------------------------------------------------
        # Определяем следующий номер материала
        #
        # Номер ищется только среди непосредственных дочерних
        # страниц текущей страницы.
        # ---------------------------------------------------------

        try:

            notebook = self.window.notebook

            from zim.notebook import Path

            current_path = self.window.page.name

            parent_path = Path(
                current_path
            )

            max_number = 0

            for record in notebook.pages.list_pages(
                parent_path
            ):

                child_name = record.name

                basename = child_name.rsplit(
                    ':',
                    1
                )[-1]

                parts = basename.split('-')

                if len(parts) < 2:
                    continue

                number_part = parts[-1]

                if not number_part.isdigit():
                    continue

                number = int(number_part)

                if number > max_number:
                    max_number = number

            number_id = str(
                max_number + 1
            ).zfill(2)

        except Exception as error:

            self._show_error(
                f'Не удалось определить номер материала:\n'
                f'{error}'
            )

            return

        # ---------------------------------------------------------
        # Формируем имя страницы
        # ---------------------------------------------------------

        page_name = (
            f'{prefix}-'
            f'{type_code}-'
            f'{level3_id}-'
            f'{level4_id}-'
            f'{number_id}'
        )

        # ---------------------------------------------------------
        # Формируем раздел «Источник»
        # ---------------------------------------------------------

        source_lines = [
            f'Автор: {author}',
            f'Название: {title}',
            f'Тип: {item_type_label}',
            f'Дата: {date}',
        ]

        for key, label_text, _ in config['source_fields']:

            clean_label = label_text.rstrip(':')

            source_lines.append(
                f'{clean_label}: {source_values.get(key, "")}'
            )

        source_lines.append(
            f'Zotero: [[zotero://select/library/items/{zotero_key}'
            '|Открыть источник в Zotero]]'
        )

        source_block = '\n'.join(source_lines)

        # ---------------------------------------------------------
        # Формируем раздел «Место»
        # ---------------------------------------------------------

        place_levels = [
            value
            for value in place_values
            if value
        ]

        place_lines = []

        if place_levels:

            place_lines.append(
                _format_location_tree(
                    place_levels
                )
            )

        if config['level4_kind'] == 'timecode':

            readable_timecode = (
                f'{level4_id[0:2]}:'
                f'{level4_id[2:4]}:'
                f'{level4_id[4:6]}'
            )

            place_lines.append(
                f'Страница: {readable_timecode}'
            )

        else:

            place_lines.append(
                f'Страница: {level4_raw}'
            )

        place_block = '\n\n'.join(
            place_lines
        )

        # ---------------------------------------------------------
        # Формируем текст страницы
        # ---------------------------------------------------------

        text = f"""====== {page_name} ======

===== Источник =====

{source_block}

===== Место =====

{place_block}

===== Фрагмент =====

{material}

===== Комментарий =====

{comment}

===== Вложения =====

"""

        # ---------------------------------------------------------
        # Создаём страницу в текущем блокноте Zim
        # ---------------------------------------------------------

        try:

            path = Path(
                f'{current_path}:{page_name}'
            )

            page_object = notebook.get_page(
                path
            )

            if page_object.exists():

                self._show_error(
                    f'Материал с таким именем уже существует:\n'
                    f'{page_name}'
                )

                return

            page_object.parse(
                'wiki',
                text
            )

            notebook.store_page(
                page_object
            )

        except Exception as error:

            self._show_error(
                f'Не удалось создать страницу материала:\n'
                f'{error}'
            )

            return

        # ---------------------------------------------------------
        # Открываем созданную страницу
        # ---------------------------------------------------------

        try:

            self.window.open_page(
                path
            )

        except Exception as error:

            self._show_error(
                f'Материал создан, но страницу не удалось открыть:\n'
                f'{error}'
            )

# =============================================================
# Ошибка
# =============================================================

    def _show_error(self, message):

        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )


        dialog.set_title(
            'Zotero'
        )


        dialog.run()
        dialog.destroy()
