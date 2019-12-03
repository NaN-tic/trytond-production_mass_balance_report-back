# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
import os
from datetime import datetime
from decimal import Decimal
from trytond.config import config
from trytond.model import fields, ModelView
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval
from trytond.wizard import Wizard, StateView, StateReport, Button
from trytond.transaction import Transaction
from trytond.modules.html_report.html_report import HTMLReport
from trytond.modules.product import price_digits


__all__ = ['Production', 'PrintProductionMassBalanceStart',
    'PrintProductionMassBalance', 'PrintProductionMassBalanceSReport']

BASE_URL = config.get('web', 'base_url')
_ZERO = 0


class Production(metaclass=PoolMeta):
    __name__ = 'production'
    balance_plan_consumption = fields.Function(fields.Float(
        'Balance Plan Consumption', digits=price_digits), 'get_mass_balance')
    balance_difference = fields.Function(fields.Float(
        'Balance Diference', digits=price_digits), 'get_mass_balance')
    balance_difference_percent = fields.Function(fields.Float(
        'Balance Diference Percent', digits=price_digits), 'get_mass_balance')

    @classmethod
    def get_mass_balance(cls, productions, names):
        res = {n: {p.id: None for p in productions} for n in names}

        for name in names:
            for production in productions:
                qty = _ZERO
                if (production.bom and production.product
                        and production.bom.inputs and production.bom.outputs):
                    product = production.product
                    quantity = production.quantity
                    uom = production.bom.outputs[0].uom
                    factor = production.bom.compute_factor(product, quantity, uom)
                    for input_ in production.bom.inputs:
                        qty += input_.compute_quantity(factor)
                    if name == 'balance_plan_consumption':
                        res[name][production.id] = qty
                    elif name == 'balance_difference':
                        digits = production.__class__.balance_difference.digits
                        res[name][production.id] = float(
                            Decimal(production.quantity - qty).quantize(
                                Decimal(str(10 ** -digits[1]))))
                    elif name == 'balance_difference_percent':
                        digits = production.__class__.balance_difference_percent.digits
                        res[name][production.id] = float(
                            Decimal((production.quantity - qty) / qty).quantize(
                                Decimal(str(10 ** -digits[1]))))
        return res


class PrintProductionMassBalanceStart(ModelView):
    'Print Production Mass Balance Start'
    __name__ = 'production.mass_balance.start'
    product = fields.Many2One('product.product', 'Product', required=True)
    from_date = fields.Date('From Date',
        # domain=[('from_date', '<', Eval('to_date'))],
        states={
            'required': Bool(Eval('to_date', False)),
        }, depends=['to_date'])
    to_date = fields.Date('To Date',
        # domain=[('to_date', '>', Eval('from_date'))],
        states={
            'required': Bool(Eval('from_date', False)),
        }, depends=['from_date'])
    type_ = fields.Selection([
        ('input', 'Input'),
        ('output', 'Output'),
        ], 'Type', required=True)

    @classmethod
    def __setup__(cls):
        super(PrintProductionMassBalanceStart, cls).__setup__()
        try:
            Lot = Pool().get('stock.lot')
        except:
            Lot = None
        if Lot:
            cls.lot = fields.Many2One('stock.lot', 'Lot',
                domain=[
                    ('product', '=', Eval('product')),
                    ],
                depends=['product'])

    @staticmethod
    def default_type_():
        return  'output'


class PrintProductionMassBalance(Wizard):
    'Print Production Mass Balance'
    __name__ = 'production.print_mass_balance'
    start = StateView('production.mass_balance.start',
        'production_mass_balance_report.print_production_mass_balance_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Print', 'print_', 'tryton-print', default=True),
            ])
    print_ = StateReport('production.mass_balance.report')

    def default_start(self, fields):
        context = Transaction().context

        res = {}
        if context.get('active_model'):
            Model = Pool().get(context['active_model'])
            id = Transaction().context['active_id']
            if Model.__name__ == 'product.template':
                template = Model(id)
                if template.products:
                    res['product'] = template.products[0].id
            elif Model.__name__ == 'stock.lot':
                lot = Model(id)
                res['lot'] = lot.id
                res['product'] = lot.product.id
        return res

    def do_print_(self, action):
        context = Transaction().context
        data = {
            'type_': self.start.type_,
            'from_date': self.start.from_date,
            'to_date': self.start.to_date,
            'product': self.start.product.id,
            'model': context.get('active_model'),
            'ids': context.get('active_ids'),
            }
        try:
            Lot = Pool().get('stock.lot')
        except:
            Lot = None
        if Lot:
            data['lot'] = self.start.lot.id if self.start.lot else None
        return action, data


class PrintProductionMassBalanceSReport(HTMLReport):
    __name__ = 'production.mass_balance.report'

    @classmethod
    def get_context(cls, records, data):
        pool = Pool()
        Company = pool.get('company.company')
        t_context = Transaction().context

        context = super().get_context(records, data)
        context['company'] = Company(t_context['company'])
        return context

    @classmethod
    def prepare(cls, data):
        pool = Pool()
        Product = pool.get('product.product')
        Move = pool.get('stock.move')
        Location = pool.get('stock.location')
        Data = pool.get('ir.model.data')

        try:
            Lot = pool.get('stock.lot')
        except:
            Lot = None

        move = Move.__table__()
        cursor = Transaction().connection.cursor()

        t_context = Transaction().context
        company_id = t_context.get('company')
        from_date = data.get('from_date') or datetime.min.date()
        to_date = data.get('to_date') or datetime.max.date()

        product = Product(data['product'])
        type_ = data['type_']

        parameters = {}
        parameters['type'] = type_
        parameters['from_date'] = from_date
        parameters['to_date'] = to_date
        parameters['show_date'] = True if data.get('from_date') else False
        parameters['product'] = product
        parameters['lot'] = Lot(data['lot']) if data.get('lot') else None
        if BASE_URL:
            base_url = '%s/#%s' % (
                BASE_URL, Transaction().database.name)
        else:
            base_url = '%s://%s/#%s' % (
                t_context['_request']['scheme'],
                t_context['_request']['http_host'],
                Transaction().database.name
                )
        parameters['base_url'] = base_url

        # Locations
        loc_data, = Data.search([
            ('module', '=', 'production'),
            ('fs_id', '=', 'location_production'),
            ('model', '=', 'stock.location'),
            ], limit=1)
        location_production = Location(loc_data.db_id)

        records = []

        # production_outs: to_location = production
        if type_ == 'input':
            sql_where = ((move.product == product.id)
                & (move.effective_date >= from_date) & (move.effective_date <= to_date)
                & (move.state == 'done') & (move.company == company_id)
                & (move.to_location == location_production.id))
        else:
            sql_where = ((move.product == product.id)
                & (move.effective_date >= from_date) & (move.effective_date <= to_date)
                & (move.state == 'done') & (move.company == company_id)
                & (move.from_location == location_production.id))

        if data.get('lot'):
            sql_where.append((move.lot == data['lot']))

        query = move.select(move.id.as_('move_id'), where=sql_where,
            order_by=move.effective_date.desc)
        cursor.execute(*query)
        move_ids = [m[0] for m in cursor.fetchall()]
        moves = Move.browse(move_ids)
        production_moves = []
        if type_ == 'input':
            productions = [m.production_input for m in moves]
            for p in productions:
                production_moves += list(p.outputs)
            quantity = sum(move.production_output.quantity
                or move.quantity for move in production_moves)
            consumption = sum(move.quantity for move in production_moves)
            plan_consumption = sum(move.production_output.balance_plan_consumption
                or _ZERO for move in production_moves)
            difference = sum(move.production_output.balance_difference
                or _ZERO for move in production_moves)
            difference_percent = sum(move.production_output.balance_difference_percent
                or _ZERO for move in production_moves)
        else:
            productions = [m.production_output for m in moves]
            for p in productions:
                production_moves += list(p.inputs)
            quantity = sum(move.production_input.quantity or move.quantity
                for move in production_moves)
            consumption = sum(move.quantity for move in production_moves)
            plan_consumption = sum(move.production_input.balance_plan_consumption
                or _ZERO for move in production_moves)
            difference = sum(move.production_input.balance_difference
                or _ZERO for move in production_moves)
            difference_percent = sum(move.production_input.balance_difference_percent
                or _ZERO for move in production_moves)

        records.append({
            'moves': production_moves,
            })
        parameters['productions'] = productions
        parameters['products'] = list(set([m.product for m in production_moves]))
        parameters['quantity'] = quantity
        parameters['consumption'] = consumption
        parameters['plan_consumption'] = plan_consumption
        parameters['difference'] = difference
        parameters['difference_percent'] = difference_percent
        return records, parameters

    @classmethod
    def execute(cls, ids, data):
        context = Transaction().context
        context['report_lang'] = Transaction().language
        context['report_translations'] = os.path.join(
            os.path.dirname(__file__), 'report', 'translations')

        with Transaction().set_context(**context):
            records, parameters = cls.prepare(data)
            return super(PrintProductionMassBalanceSReport, cls).execute(ids, {
                    'name': 'production.mass_balance.report',
                    'model': data['model'],
                    'records': records,
                    'parameters': parameters,
                    'output_format': 'html',
                    'report_options': {
                        'now': datetime.now(),
                        }
                    })
