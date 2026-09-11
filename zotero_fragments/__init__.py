# -*- coding: utf-8 -*-

from gi.repository import Gtk, Pango

from zim.config import ConfigManager
from zim.plugins import PluginClass
from zim.actions import action
from zim.gui.mainwindow import MainWindowExtension
from zim.notebook import get_notebook_list


def _get_notebook_choices():
    """Return registered Zim notebooks as (URI, name) pairs."""
    notebooks = get_notebook_list()

    return [
        (notebook.uri, notebook.name)
        for notebook in notebooks
    ]


NOTEBOOK_CHOICES = _get_notebook_choices()


def _is_material_page_name(name):
    """
    Проверяет, соответствует ли имя страницы формату Material ID.

    Формат:
        AUTHOR-TITLE-TYPE-LEVEL3-LEVEL4-NUMBER

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


def _get_material_fragment(notebook, material_name):
    """
    Возвращает Fragment конкретного Material page.
    """

    from zim.notebook import Path

    page = notebook.get_page(
        Path(material_name)
    )

    return _extract_fragment(page)


def _material_link(
    material_name,
    link_data,
):
    """
    Формирует Zim-ссылку на Material page
    с библиографической подписью.
    """

    author = link_data['author']
    title = link_data['title']
    volume = link_data['volume']
    location = link_data['location']

    # Автор + название
    if author:
        label = f'{author} "{title}"'
    else:
        label = f'"{title}"'

    # Том
    if volume:
        label += f', т. {volume}'

    # Место
    if location:

        if location.count(':') == 2:
            # Таймкод HH:MM:SS
            label += f', {location}'
        else:
            # Номер страницы
            label += f', стр. {location}'

    return (
        f'[[cite?{material_name}|{label}]]'
    )


def _get_material_link_data(notebook, material_name):
    """
    Извлекает данные из секций Источник и Место
    для формирования подписи ссылки на Material.
    """

    from zim.notebook import Path

    page = notebook.get_page(
        Path(material_name)
    )

    lines = page.dump('wiki')

    data = {
        'author': '',
        'title': '',
        'volume': '',
        'location': '',
    }

    section = None

    for line in lines:

        if line == '===== Источник =====\n':
            section = 'source'
            continue

        if line == '===== Место =====\n':
            section = 'place'
            continue

        if (
            line.startswith('===== ')
            and line.rstrip().endswith(' =====')
        ):
            section = None
            continue

        if section == 'source':

            if line.startswith('Автор:'):
                data['author'] = (
                    line[len('Автор:'):].strip()
                )

            elif line.startswith('Название:'):
                data['title'] = (
                    line[len('Название:'):].strip()
                )

            elif line.startswith('Том:'):
                data['volume'] = (
                    line[len('Том:'):].strip()
                )

        elif section == 'place':

            if line.startswith('Страница:'):
                data['location'] = (
                    line[len('Страница:'):].strip()
                )

    return data


def _insert_material_fragment(
    notebook,
    material_name,
    textview,
):
    """
    Вставляет Fragment выбранного Material page
    и ссылку на этот Material в текущую позицию курсора.
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

    link_data = _get_material_link_data(
        notebook,
        material_name,
    )

    link = _material_link(
        material_name,
        link_data,
    )

    text = (
        '"'
        + fragment
        + '"'
        + '\n\n'
        + link
    )

    buffer = textview.get_buffer()

    buffer.insert_at_cursor(
        text
    )


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


class ZoteroFragmentsPlugin(PluginClass):

    plugin_info = {
        'name': 'Zotero Fragments',
        'description': 'Work with Material and Fragment records from Zotero.',
        'author': 'Nikolay',
    }

    plugin_preferences = (
        (
            'citations_notebook',
            'choice',
            'Citations notebook',
            NOTEBOOK_CHOICES[0][0] if NOTEBOOK_CHOICES else '',
            NOTEBOOK_CHOICES,
        ),
    )


class ZoteroFragmentsMainWindowExtension(MainWindowExtension):

    def __init__(self, plugin, window):
        super().__init__(plugin, window)

        # print(
            # 'ZOTERO FRAGMENTS: current notebook =',
            # window.notebook
        # )

        citations_uri = plugin.preferences['citations_notebook']

        self.zotero_insert_fragment.set_sensitive(
            window.notebook.uri != citations_uri
        )

        # print(
            # 'ZOTERO FRAGMENTS: citations URI =',
            # citations_uri
        # )

        from zim.notebook import resolve_notebook
        from zim.notebook import build_notebook

        info = resolve_notebook(citations_uri)

        # print(
            # 'ZOTERO FRAGMENTS: resolved citations notebook =',
            # info
        # )

        citations_notebook, _ = build_notebook(info)

        # print(
            # 'ZOTERO FRAGMENTS: citations notebook object =',
            # citations_notebook
        # )

        try:
            materials = _find_material_pages(
                citations_notebook
            )

            # print(
                # 'ZOTERO FRAGMENTS: Material pages =',
                # materials
            # )

        except Exception as error:
            print(
                'ZOTERO FRAGMENTS: ERROR in _find_material_pages =',
                repr(error)
            )

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

    @action('Zotero Insert Fragment', menuhints='tools')
    def zotero_insert_fragment(self):

        try:
            work_notebook = self.window.notebook

            citations_uri = self.plugin.preferences[
                'citations_notebook'
            ]

            from zim.notebook import resolve_notebook
            from zim.notebook import build_notebook

            info = resolve_notebook(
                citations_uri
            )

            citations_notebook, _ = build_notebook(
                info
            )

            materials = _find_material_pages(
                citations_notebook
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
                citations_notebook,
                selected,
                textview,
            )

        except Exception as error:

            self._show_error(
                f'Ошибка вставки Fragment:\n{error}'
            )
