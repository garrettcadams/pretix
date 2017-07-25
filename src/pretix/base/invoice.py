from collections import defaultdict
from decimal import Decimal
from io import BytesIO
from typing import Tuple

from django.contrib.staticfiles import finders
from django.dispatch import receiver
from django.utils.formats import date_format, localize
from django.utils.translation import pgettext
from reportlab.lib import pagesizes
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, StyleSheet1
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, NextPageTemplate, PageTemplate, Paragraph, Spacer,
    Table, TableStyle,
)

from pretix.base.models import Event, Invoice
from pretix.base.signals import register_invoice_renderers


class BaseInvoiceRenderer:
    """
    This is the base class for all invoice renderers.
    """

    def __init__(self, event: Event):
        self.event = event

    def __str__(self):
        return self.identifier

    def generate(self, invoice: Invoice) -> Tuple[str, str, str]:
        """
        This method should generate the invoice file and return a tuple consisting of a
        filename, a file type and file content. The extension will be taken from the filename
        which is otherwise ignored.
        """
        raise NotImplementedError()

    @property
    def verbose_name(self) -> str:
        """
        A human-readable name for this renderer. This should be short but
        self-explanatory. Good examples include 'German DIN 5008' or 'Italian invoice'.
        """
        raise NotImplementedError()  # NOQA

    @property
    def identifier(self) -> str:
        """
        A short and unique identifier for this renderer.
        This should only contain lowercase letters and in most
        cases will be the same as your package name.
        """
        raise NotImplementedError()  # NOQA


class BaseReportlabInvoiceRenderer(BaseInvoiceRenderer):
    """
    This is a convenience class to avoid duplicate code when implementing invoice renderers
    that are based on reportlab.
    """
    pagesize = pagesizes.A4
    left_margin = 25 * mm
    right_margin = 20 * mm
    top_margin = 20 * mm
    bottom_margin = 15 * mm
    doc_template_class = BaseDocTemplate

    def _init(self):
        """
        Initialize the renderer. By default, this registers fonts and sets ``self.stylesheet``.
        """
        self.stylesheet = self._get_stylesheet()
        self._register_fonts()

    def _get_stylesheet(self):
        """
        Get a stylesheet. By default, this contains the "Normal" and "Heading1" styles.
        """
        stylesheet = StyleSheet1()
        stylesheet.add(ParagraphStyle(name='Normal', fontName='OpenSans', fontSize=10, leading=12))
        stylesheet.add(ParagraphStyle(name='Heading1', fontName='OpenSansBd', fontSize=15, leading=15 * 1.2))
        stylesheet.add(ParagraphStyle(name='FineprintHeading', fontName='OpenSansBd', fontSize=8, leading=12))
        return stylesheet

    def _register_fonts(self):
        """
        Register fonts with reportlab. By default, this registers the OpenSans font family
        """
        pdfmetrics.registerFont(TTFont('OpenSans', finders.find('fonts/OpenSans-Regular.ttf')))
        pdfmetrics.registerFont(TTFont('OpenSansIt', finders.find('fonts/OpenSans-Italic.ttf')))
        pdfmetrics.registerFont(TTFont('OpenSansBd', finders.find('fonts/OpenSans-Bold.ttf')))
        pdfmetrics.registerFont(TTFont('OpenSansBI', finders.find('fonts/OpenSans-BoldItalic.ttf')))
        pdfmetrics.registerFontFamily('OpenSans', normal='OpenSans', bold='OpenSansBd',
                                      italic='OpenSansIt', boldItalic='OpenSansBI')

    def _on_other_page(self, canvas: Canvas, doc):
        """
        Called when a new page is rendered that is *not* the first page.
        """
        pass

    def _on_first_page(self, canvas: Canvas, doc):
        """
        Called when a new page is rendered that is the first page.
        """
        pass

    def _get_story(self, doc):
        """
        Called to create the story to be inserted into the main frames.
        """
        raise NotImplementedError()

    def _get_first_page_frames(self, doc):
        """
        Called to create a list of frames for the first page.
        """
        return [
            Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height - 75 * mm,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                  id='normal')
        ]

    def _get_other_page_frames(self, doc):
        """
        Called to create a list of frames for the other pages.
        """
        return [
            Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                  id='normal')
        ]

    def _build_doc(self, fhandle):
        """
        Build a PDF document in a given file handle
        """
        self._init()
        doc = self.doc_template_class(fhandle, pagesize=self.pagesize,
                                      leftMargin=self.left_margin, rightMargin=self.right_margin,
                                      topMargin=self.top_margin, bottomMargin=self.bottom_margin)

        doc.addPageTemplates([
            PageTemplate(
                id='FirstPage',
                frames=self._get_first_page_frames(doc),
                onPage=self._on_first_page,
                pagesize=self.pagesize
            ),
            PageTemplate(
                id='OtherPages',
                frames=self._get_other_page_frames(doc),
                onPage=self._on_other_page,
                pagesize=self.pagesize
            )
        ])
        story = self._get_story(doc)
        doc.build(story)
        return doc

    def generate(self, invoice: Invoice):
        self.invoice = invoice
        buffer = BytesIO()
        self._build_doc(buffer)
        buffer.seek(0)
        return 'invoice.pdf', 'application/pdf', buffer.read()


class ClassicInvoiceRenderer(BaseReportlabInvoiceRenderer):
    identifier = 'classic'
    verbose_name = pgettext('invoice', 'Classic renderer (pretix 1.0)')

    def _on_other_page(self, canvas: Canvas, doc):
        canvas.saveState()
        canvas.setFont('OpenSans', 8)
        canvas.drawRightString(self.pagesize[0] - 20 * mm, 10 * mm, pgettext("invoice", "Page %d") % (doc.page,))

        for i, line in enumerate(self.invoice.footer_text.split('\n')[::-1]):
            canvas.drawCentredString(self.pagesize[0] / 2, 25 + (3.5 * i) * mm, line.strip())

        canvas.restoreState()

    def _on_first_page(self, canvas: Canvas, doc):
        canvas.setCreator('pretix.eu')
        canvas.setTitle(pgettext('invoice', 'Invoice {num}').format(num=self.invoice.number))

        canvas.saveState()
        canvas.setFont('OpenSans', 8)
        canvas.drawRightString(self.pagesize[0] - 20 * mm, 10 * mm, pgettext("invoice", "Page %d") % (doc.page,))

        for i, line in enumerate(self.invoice.footer_text.split('\n')[::-1]):
            canvas.drawCentredString(self.pagesize[0] / 2, 25 + (3.5 * i) * mm, line.strip())

        textobject = canvas.beginText(25 * mm, (297 - 15) * mm)
        textobject.setFont('OpenSansBd', 8)
        textobject.textLine(pgettext('invoice', 'Invoice from').upper())
        canvas.drawText(textobject)

        p = Paragraph(self.invoice.invoice_from.strip().replace('\n', '<br />\n'), style=self.stylesheet['Normal'])
        p.wrapOn(canvas, 70 * mm, 50 * mm)
        p_size = p.wrap(70 * mm, 50 * mm)
        p.drawOn(canvas, 25 * mm, (297 - 17) * mm - p_size[1])

        textobject = canvas.beginText(25 * mm, (297 - 50) * mm)
        textobject.setFont('OpenSansBd', 8)
        textobject.textLine(pgettext('invoice', 'Invoice to').upper())
        canvas.drawText(textobject)

        p = Paragraph(self.invoice.invoice_to.strip().replace('\n', '<br />\n'), style=self.stylesheet['Normal'])
        p.wrapOn(canvas, 85 * mm, 50 * mm)
        p_size = p.wrap(85 * mm, 50 * mm)
        p.drawOn(canvas, 25 * mm, (297 - 52) * mm - p_size[1])

        textobject = canvas.beginText(125 * mm, (297 - 38) * mm)
        textobject.setFont('OpenSansBd', 8)
        textobject.textLine(pgettext('invoice', 'Order code').upper())
        textobject.moveCursor(0, 5)
        textobject.setFont('OpenSans', 10)
        textobject.textLine(self.invoice.order.full_code)
        canvas.drawText(textobject)

        textobject = canvas.beginText(125 * mm, (297 - 50) * mm)
        textobject.setFont('OpenSansBd', 8)
        if self.invoice.is_cancellation:
            textobject.textLine(pgettext('invoice', 'Cancellation number').upper())
            textobject.moveCursor(0, 5)
            textobject.setFont('OpenSans', 10)
            textobject.textLine(self.invoice.number)
            textobject.moveCursor(0, 5)
            textobject.setFont('OpenSansBd', 8)
            textobject.textLine(pgettext('invoice', 'Original invoice').upper())
            textobject.moveCursor(0, 5)
            textobject.setFont('OpenSans', 10)
            textobject.textLine(self.invoice.refers.number)
        else:
            textobject.textLine(pgettext('invoice', 'Invoice number').upper())
            textobject.moveCursor(0, 5)
            textobject.setFont('OpenSans', 10)
            textobject.textLine(self.invoice.number)
        textobject.moveCursor(0, 5)

        if self.invoice.is_cancellation:
            textobject.setFont('OpenSansBd', 8)
            textobject.textLine(pgettext('invoice', 'Cancellation date').upper())
            textobject.moveCursor(0, 5)
            textobject.setFont('OpenSans', 10)
            textobject.textLine(date_format(self.invoice.date, "DATE_FORMAT"))
            textobject.moveCursor(0, 5)
            textobject.setFont('OpenSansBd', 8)
            textobject.textLine(pgettext('invoice', 'Original invoice date').upper())
            textobject.moveCursor(0, 5)
            textobject.setFont('OpenSans', 10)
            textobject.textLine(date_format(self.invoice.refers.date, "DATE_FORMAT"))
            textobject.moveCursor(0, 5)
        else:
            textobject.setFont('OpenSansBd', 8)
            textobject.textLine(pgettext('invoice', 'Invoice date').upper())
            textobject.moveCursor(0, 5)
            textobject.setFont('OpenSans', 10)
            textobject.textLine(date_format(self.invoice.date, "DATE_FORMAT"))
            textobject.moveCursor(0, 5)

        canvas.drawText(textobject)

        if self.invoice.event.settings.invoice_logo_image:
            logo_file = self.invoice.event.settings.get('invoice_logo_image', binary_file=True)
            canvas.drawImage(ImageReader(logo_file),
                             95 * mm, (297 - 38) * mm,
                             width=25 * mm, height=25 * mm,
                             preserveAspectRatio=True, anchor='n',
                             mask='auto')

        if self.invoice.event.settings.show_date_to:
            p_str = (
                str(self.invoice.event.name) + '\n' + pgettext('invoice', '{from_date}\nuntil {to_date}').format(
                    from_date=self.invoice.event.get_date_from_display(),
                    to_date=self.invoice.event.get_date_to_display())
            )
        else:
            p_str = (
                str(self.invoice.event.name) + '\n' + self.invoice.event.get_date_from_display()
            )

        p = Paragraph(p_str.strip().replace('\n', '<br />\n'), style=self.stylesheet['Normal'])
        p.wrapOn(canvas, 65 * mm, 50 * mm)
        p_size = p.wrap(65 * mm, 50 * mm)
        p.drawOn(canvas, 125 * mm, (297 - 17) * mm - p_size[1])

        textobject = canvas.beginText(125 * mm, (297 - 15) * mm)
        textobject.setFont('OpenSansBd', 8)
        textobject.textLine(pgettext('invoice', 'Event').upper())
        canvas.drawText(textobject)

        canvas.restoreState()

    def _get_first_page_frames(self, doc):
        footer_length = 3.5 * len(self.invoice.footer_text.split('\n')) * mm
        return [
            Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height - 75 * mm,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=footer_length,
                  id='normal')
        ]

    def _get_other_page_frames(self, doc):
        footer_length = 3.5 * len(self.invoice.footer_text.split('\n')) * mm
        return [
            Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=footer_length,
                  id='normal')
        ]

    def _get_story(self, doc):
        story = [
            NextPageTemplate('FirstPage'),
            Paragraph(pgettext('invoice', 'Invoice')
                      if not self.invoice.is_cancellation
                      else pgettext('invoice', 'Cancellation'),
                      self.stylesheet['Heading1']),
            Spacer(1, 5 * mm),
            NextPageTemplate('OtherPages'),
        ]

        if self.invoice.introductory_text:
            story.append(Paragraph(self.invoice.introductory_text, self.stylesheet['Normal']))
            story.append(Spacer(1, 10 * mm))

        taxvalue_map = defaultdict(Decimal)
        grossvalue_map = defaultdict(Decimal)

        tstyledata = [
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (-1, 0), 'OpenSansBd'),
            ('FONTNAME', (0, -1), (-1, -1), 'OpenSansBd'),
            ('LEFTPADDING', (0, 0), (0, -1), 0),
            ('RIGHTPADDING', (-1, 0), (-1, -1), 0),
        ]
        tdata = [(
            pgettext('invoice', 'Description'),
            pgettext('invoice', 'Tax rate'),
            pgettext('invoice', 'Net'),
            pgettext('invoice', 'Gross'),
        )]
        total = Decimal('0.00')
        for line in self.invoice.lines.all():
            tdata.append((
                Paragraph(line.description, self.stylesheet['Normal']),
                localize(line.tax_rate) + " %",
                localize(line.net_value) + " " + self.invoice.event.currency,
                localize(line.gross_value) + " " + self.invoice.event.currency,
            ))
            taxvalue_map[line.tax_rate, line.tax_name] += line.tax_value
            grossvalue_map[line.tax_rate, line.tax_name] += line.gross_value
            total += line.gross_value

        tdata.append([
            pgettext('invoice', 'Invoice total'), '', '', localize(total) + " " + self.invoice.event.currency
        ])
        colwidths = [a * doc.width for a in (.55, .15, .15, .15)]
        table = Table(tdata, colWidths=colwidths, repeatRows=1)
        table.setStyle(TableStyle(tstyledata))
        story.append(table)

        story.append(Spacer(1, 15 * mm))

        if self.invoice.payment_provider_text:
            story.append(Paragraph(self.invoice.payment_provider_text, self.stylesheet['Normal']))

        if self.invoice.additional_text:
            story.append(Paragraph(self.invoice.additional_text, self.stylesheet['Normal']))
            story.append(Spacer(1, 15 * mm))

        tstyledata = [
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (0, -1), 0),
            ('RIGHTPADDING', (-1, 0), (-1, -1), 0),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, -1), 'OpenSans'),
        ]
        tdata = [[
            pgettext('invoice', 'Tax rate'),
            pgettext('invoice', 'Net value'),
            pgettext('invoice', 'Gross value'),
            pgettext('invoice', 'Tax'),
            ''
        ]]

        for idx, gross in grossvalue_map.items():
            rate, name = idx
            if rate == 0:
                continue
            tax = taxvalue_map[idx]
            tdata.append([
                localize(rate) + " % " + name,
                localize(gross - tax) + " " + self.invoice.event.currency,
                localize(gross) + " " + self.invoice.event.currency,
                localize(tax) + " " + self.invoice.event.currency,
                ''
            ])

        if len(tdata) > 2:
            story.append(Paragraph(pgettext('invoice', 'Included taxes'), self.stylesheet['FineprintHeading']))
            colwidths = [a * doc.width for a in (.25, .15, .15, .15, .3)]
            table = Table(tdata, colWidths=colwidths, repeatRows=2, hAlign=TA_LEFT)
            table.setStyle(TableStyle(tstyledata))
            story.append(table)
        return story


@receiver(register_invoice_renderers, dispatch_uid="invoice_renderer_classic")
def recv_classic(sender, **kwargs):
    return ClassicInvoiceRenderer
