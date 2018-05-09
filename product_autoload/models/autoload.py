# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging
from time import time
from datetime import datetime, timedelta
import csv
from mappers import MAP_WRITE_DATE
from openerp import api, models, fields
from mappers import ProductMapper

_logger = logging.getLogger(__name__)

SECTION = 'section.csv'
FAMILY = 'family.csv'
ITEM = 'item.csv'
DATA = 'data.csv'
PRODUCTCODE = 'productcode.csv'

IM_CODE = 0
IM_NAME = 1
IM_ORIGIN = 2
IM_SECTION_CODE = 3
IM_FAMILY_CODE = 4
IM_MARGIN = 5
IM_LEN = 6

PC_BARCODE = 0
PC_PRODUCT_CODE = 1
PC_UXB = 2
PC_LEN = 3


class AutoloadMgr(models.Model):
    _name = 'product_autoload.manager'
    _description = "Manage product data import"

    name = fields.Char()
    start_time = fields.Datetime(

    )
    elapsed_time = fields.Datetime(

    )
    statistics = fields.Html(

    )

    @api.model
    def check_garbage(self):
        _logger.info(
            '/////////////////////////////////////////////////////////')

        from bisect import bisect_left

        data = []
        with open('/opt/odoo/data/product_data/data.csv', 'r') as file_csv:
            reader = csv.reader(file_csv)
            for line in reader:
                data.append(line[0])
        data.sort()
        len1 = len(data)
        # esta en la bd y no esta en el archivo
        prod_obj = self.env['product.template']
        for prod in prod_obj.search([]):
            default_code = prod.default_code
            i = bisect_left(data, default_code)
            if i < len1 and i >= 0:
                _logger.info('{} ------- {}'.format(i, default_code))
                if data[i] != default_code:
                    prod.warranty = 99

        _logger.info(
            '/////////////////////////////////////////////////////////')

    @staticmethod
    def load_section(data_path):
        """ Carga la estructura de datos en memoria
        """
        res = dict()
        with open(data_path + SECTION, 'r') as file_csv:
            reader = csv.reader(file_csv)
            for line in reader:
                _logger.info('loading section {}'.format(line[1]))
                res[line[0]] = line[1]
        return res

    @staticmethod
    def load_family(data_path):
        """ Carga la estructura de datos en memoria
        """
        res = dict()
        with open(data_path + FAMILY, 'r') as file_csv:
            reader = csv.reader(file_csv)
            for line in reader:
                _logger.info('loading family {}'.format(line[1]))
                res[line[0]] = line[1]
        return res

    @api.multi
    def load_item(self, data_path, item=ITEM):
        """ Carga los datos en un modelo, chequeando por modificaciones
            Si cambio el precio recalcula todos precios de los productos
        """
        prod_obj = self.env['product.template']
        item_obj = self.env['product_autoload.item']

        with open(data_path + item, 'r') as file_csv:
            reader = csv.reader(file_csv)
            for line in reader:
                _logger.info('loading item {}'.format(line[IM_NAME]))
                values = {
                    'code': line[IM_CODE].strip(),
                    'name': line[IM_NAME].strip(),
                    'origin': line[IM_CODE].strip(),
                    'section': self._section[line[IM_SECTION_CODE]].strip(),
                    'family': self._family[line[IM_FAMILY_CODE]].strip(),
                    'margin': line[IM_MARGIN].strip()
                }
                # buscar el codigo en la tabla
                item = item_obj.search([('code', '=', values['code'])])
                if item:
                    if not (item.origin == values['origin'] and
                                    item.section == values['section'] and
                                    item.family == values['family'] and
                                    item.margin == float(values['margin'])):
                        item.write(values)

                        ## forzar recalculo de precios.
                        prod = prod_obj.search([
                            ('item_code', '=', values['code'])])
                        if prod:
                            prod.recalculate_list_price(item.margin)
                else:
                    item_obj.create(values)

                    # forzar recalculo de precios
                    prod = prod_obj.search([
                        ('item_code', '=', values['code'])])
                    if prod:
                        prod.recalculate_list_price(item.margin)
                        _logger.info('recalculate for create {}'
                                     ''.format(item.code))

    @api.multi
    def load_productcode(self, data_path):
        """ Borra la tabla productcode y la vuelve a crear con los datos nuevos
        """
        item_obj = self.env['product_autoload.productcode']
        item_obj.search([]).unlink()
        count = 0
        with open(data_path + PRODUCTCODE, 'r') as file_csv:
            reader = csv.reader(file_csv)
            for line in reader:
                count += 1
                if count == 2000:
                    count = 0
                    _logger.info('loading +2000 barcodes')
                values = {
                    'barcode': line[PC_BARCODE].strip(),
                    'product_code': line[PC_PRODUCT_CODE].strip(),
                    'uxb': line[PC_UXB].strip(),
                }
                item_obj.create(values)

    @api.multi
    def load_product(self, data_path):
        """ Carga todos los productos teniendo en cuenta la fecha
        """
        bulonfer = self.env['res.partner'].search(
            [('ref', '=', 'BULONFER')])
        if not bulonfer:
            raise Exception('Vendor Bulonfer not found')

        last_replication = self.last_replication
        supplierinfo = self.env['product.supplierinfo']
        self.prod_processed = 0
        with open(data_path + DATA, 'r') as file_csv:
            reader = csv.reader(file_csv)
            for line in reader:
                if line and line[MAP_WRITE_DATE] > last_replication:
                    obj = ProductMapper(line, data_path, bulonfer,
                                        supplierinfo)
                    obj.execute(self.env)
                    self.prod_processed += 1

    @api.model
    def run(self, item=ITEM):
        """ Actualiza todos los productos.
        """
        _logger.info('REPLICATION: Start')
        # empezamos a contar el tiempo de proceso
        start_time = time()
        data_path = self.data_path

        rec = self.create({'name': 'Inicia Proceso'})
        try:
            self.send_email('Replicacion Bulonfer #{}, Inicio'.format(rec.id),
                            'Se inicio el proceso',
                            self.email_from, self.email_to)

            _logger.info('REPLICATION: Load memoy tables')
            # Cargar en memoria las tablas chicas
            self._section = self.load_section(data_path)
            self._family = self.load_family(data_path)
            self.prod_processed = 0

            _logger.info('REPLICATION: Load disk tables')
            # Cargar en bd las demas tablas
            self.load_item(data_path, item)
            self.load_productcode(data_path)

            # Aca carga solo los productos que tienen fecha de modificacion
            # posterior a la fecha de proceso y los actualiza o los crea segun
            # sea necesario
            _logger.info('REPLICATION: Load products')
            self.load_product(data_path)

            # terminamos de contar el tiempo de proceso
            elapsed_time = time() - start_time

            rec.stats = self.get_stats(elapsed_time)
            self.send_email('Replicacion Bulonfer #{}, Fin'.format(rec.id),
                            self.get_stats(elapsed_time),
                            self.email_from, self.email_to)

            self.last_replication = str(datetime.now())
            _logger.info('REPLICATION: End')

        except Exception as ex:
            _logger.error('Replicacion Bulonfer {}'.format(ex.message))
            self.send_email('Replicacion Bulonfer #{}, '
                            'ERROR'.format(rec.id), ex.message,
                            self.email_from, self.email_to)
            raise

    @api.model
    def update_categories(self):
        # linkear las categorias
        _logger.info('update categories')
        categ_obj = self.env['product.category']
        item_obj = self.env['product_autoload.item']

        prods = self.env['product.template'].search(
            [('invalidate_category', '=', True)], limit=400)
        for prod in prods:
            # buscar el item que corresponde al producto
            item = item_obj.search([('code', '=', prod.item_code)])
            if not item:
                text = 'product {} has item = {} but there is no such item ' \
                       'in item.csv'.format(prod.default_code, prod.item_code)
                self.send_email('Replicacion Bulonfer, ERROR', text,
                                self.email_from, self.email_to)
                raise Exception(text)

            # calcular el precio de lista
            prod.recalculate_list_price(item.margin)

            # buscar seccion o crearla en categorias
            sec_id = categ_obj.search([('name', '=', item.section),
                                       ('parent_id', '=', False)])
            if not sec_id:
                sec_id = categ_obj.create({'name': item.section})

            # buscar seccion / familia o crearla
            sec_fam_id = categ_obj.search([('name', '=', item.family),
                                           ('parent_id.name', '=',
                                            item.section)])
            if not sec_fam_id:
                sec_fam_id = categ_obj.create({'name': item.family,
                                               'parent_id': sec_id.id})

            # buscar seccion / familia / item o crearla
            categ_id = categ_obj.search([('name', '=', item.name),
                                         ('parent_id.name', '=', item.family),
                                         ('parent_id.parent_id.name', '=',
                                          item.section)])
            if not categ_id:
                categ_id = categ_obj.create({'name': item.name,
                                             'parent_id': sec_fam_id.id})
            _logger.info('Setting {} --> {}'.format(
                prod.default_code, categ_id.complete_name))
            prod.write(
                {
                    'categ_id': categ_id.id,
                    'invalidate_category': False
                }
            )

    @api.multi
    def send_email(self, subject, body, email_from, email_to):
        email_to = email_to.split(',')
        if len(email_to) == 0:
            _logger.error('No hay destinatario de mail')
            return

        smtp = self.env['ir.mail_server']
        try:
            message = smtp.build_email(email_from, email_to, subject, body)
            smtp.send_email(message)
        except Exception as ex:
            _logger.error('Falla envio de mail {}'.format(ex.message))

    @api.multi
    def get_stats(self, elapsed_time):
        elapsed = str(timedelta(seconds=elapsed_time))

        ret = u'Terminó el proceso\n'
        ret += u'Duración: {}\n'.format(elapsed)
        ret += u'Productos procesados: {}'.format(self.prod_processed)
        return ret

    @property
    def email_from(self):
        return self.env['ir.config_parameter'].get_param('email_from', '')

    @property
    def email_to(self):
        return self.env['ir.config_parameter'].get_param('email_notification',
                                                         '')

    @property
    def data_path(self):
        return self.env['ir.config_parameter'].get_param('data_path', '')

    @property
    def last_replication(self):
        """ Si import_only_new devolver ulima replicacion en el 2000
            Si no, devolver la fecha de la ultima replicacion
        """
        parameter_obj = self.env['ir.config_parameter']
        if not parameter_obj.get_param('import_only_new'):
            return '2000-01-01'
        else:
            return parameter_obj.get_param('last_replication')

    @last_replication.setter
    def last_replication(self, value):
        parameter_obj = self.env['ir.config_parameter']
        parameter_obj.set_param('last_replication', str(value))

    @api.model
    def process_invoice_discounts(self):
        invoices = self.env['account.invoice'].search(
            [('discount_processed', '=', False),
             ('partner_id.ref', '=', 'BULONFER'),
             ('state', 'in', ['open', 'paid']),
             ('type', '=', 'in_invoice')])

        for invoice in invoices:
            _logger.info('processing discounts on invoice '
                         '{}'.format(invoice.document_number))
            invoice.compute_invoice_discount()
