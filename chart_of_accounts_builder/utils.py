from __future__ import unicode_literals

import frappe, json, os, tarfile
from frappe import _
from frappe.utils import cint, random_string
from frappe.model.naming import append_number_if_name_exists
from frappe.model.rename_doc import rename_doc
from erpnext.accounts.utils import add_ac
from erpnext.setup.doctype.company.delete_company_transactions import delete_company_transactions
from erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts \
	import get_charts_for_country, get_account_tree_from_existing_company
from erpnext.accounts.doctype.account.account import update_account_number
from six import string_types

def setup_charts(delete_existing=True):
	frappe.local.flags.allow_unverified_charts = True

	# delete
	if delete_existing:
		for company in frappe.get_all("Company"):
			if company.name not in ("Wind Power LLC", "Test Company"):
				print("deleting {0}".format(company.name))
				frappe.delete_doc("Company", company.name)
				frappe.db.commit()

	print("-"*40)
	for country in frappe.get_all("Country", fields=["name", "code"], filters={"name": "India"}):
		charts = get_charts_for_country(country.name)
		for i, chart in enumerate(charts):
			if (chart != "Standard" or country.name == "United States"):
				if not frappe.db.exists("Company", chart):
					print(chart.encode('utf-8'))

					company = frappe.new_doc("Company")
					company.company_name = chart
					company.country = country.name
					company.chart_of_accounts = chart
					company.abbr = country.code + str(i+1)
					company.default_currency = "USD"
					company.insert()
					frappe.db.commit()

@frappe.whitelist()
def update_account(args=None):
	frappe.local.flags.allow_unverified_charts = True
	if not args:
		args = frappe.local.form_dict
		args.pop("cmd")
	if not args.get("account_type"):
		args.account_type = None
	args.is_group = cint(args.is_group)
	account = frappe.get_doc("Account", args.name)
	account.update(args)
	account.flags.ignore_permissions = True
	if args.get("is_root"):
		account.flags.ignore_mandatory = True

	account.save()
	disable_submitted(args.company)

	frappe.local.flags.allow_unverified_charts = False

@frappe.whitelist()
def add_account(args=None):
	if not args:
		args = frappe.local.form_dict

	account_name = add_ac(args)
	if account_name:
		disable_submitted(args.company)
	return account_name

@frappe.whitelist()
def rename_account(company, old_name, new_account_name, new_account_number):
	update_account_number(old_name, new_account_name, new_account_number)
	disable_submitted(company)

@frappe.whitelist()
def delete_account(account, company):
	frappe.delete_doc("Account", account, ignore_permissions=True)
	disable_submitted(company)

@frappe.whitelist()
def fork(company):
	ref_company = frappe.get_doc("Company", company)
	fork = create_company(ref_company.forked_from or ref_company.name, ref_company.country,
		ref_company.default_currency, ref_company.chart_of_accounts, ref_company.name)

	return fork

def create_company(company_name, country, default_currency, chart_of_accounts, forked_from=None):
	frappe.local.flags.allow_unverified_charts = True

	company = frappe.new_doc("Company")
	company.country = country
	company.default_currency = default_currency
	company.chart_of_accounts = chart_of_accounts
	company.abbr = random_string(3)
	company.forked = 1
	company.forked_from = forked_from
	numbered_company_name = append_number_if_name_exists("Company", company_name)
	company.company_name = numbered_company_name
	company.name = numbered_company_name

	company.flags.ignore_permissions = True

	company.insert(ignore_permissions=True)

	if frappe.local.message_log:
		frappe.local.message_log = []

	frappe.local.flags.allow_unverified_charts = False

	return company.name

@frappe.whitelist()
def submit_chart(company, chart_of_accounts_name, domain=None):
	validate_roots(company)
	validate_account_types(company)
	validate_accounts(company)

	if frappe.db.get_value("Company", {"name": ["!=", company],
		"chart_of_accounts_name": chart_of_accounts_name}, "chart_of_accounts_name"):
		frappe.throw(_("Chart of Acconuts with this name already exist. Please select a different name."))

	frappe.db.set_value("Company", company, "submitted", 1)
	frappe.db.set_value("Company", company, "chart_of_accounts_name", chart_of_accounts_name)
	if domain:
		frappe.db.set_value("Company", company, "domain", domain)

	frappe.cache().hset("init_details", frappe.session.user, {})
	notify_frappe_team(company)

@frappe.whitelist()
def delete_chart(company):
	# delete company and associated chart of accounts
	delete_company_transactions(company)
	frappe.delete_doc('Company', company)
	frappe.cache().hset("init_details", frappe.session.user, {})

def notify_frappe_team(company):
	pass
	# subject = "New Chart of Accounts {chart_name} submitted".format(chart_name=company)
	# message = """
	# 	New Chart of Accounts: {chart_name}
	# 	Country: {country}
	# 	Submitted By: {user}
	# """.format(chart_name=company,
	# 	country=frappe.db.get_value("Company", company, "country"),
	# 	user=frappe.session.user)

	# frappe.sendmail(recipients="developers@erpnext.com", subject=subject, message=message)

@frappe.whitelist()
def email_comment(company, comment):
	pass
	# subject = _("New comment on Charts - {0}").format(company)
	# message = _("{0} <small>by {1}</small>").format(comment, frappe.session.user)
	# message += "<p><a href='chart?company={0}' style='font-size: 80%'>{1}</a></p>"\
	# 	.format(company, _("View it in your browser"))

	# frappe.sendmail(recipients="developers@erpnext.com", subject=subject, message=message)

def validate_roots(company):
	roots = frappe.db.sql("""select account_name, root_type from tabAccount
		where company=%s and ifnull(parent_account, '') = ''""", company, as_dict=1)
	if len(roots) < 4:
		frappe.throw(_("Number of root accounts cannot be less than 4"))

	for account in roots:
		if not account.root_type:
			frappe.throw(_("Please enter Root Type for {0}").format(account.account_name))
		elif account.root_type not in ("Asset", "Liability", "Expense", "Income", "Equity"):
			frappe.throw(_("Root Type for {0} must be one of the Asset, Liability, Income, Expense and Equity").format(account.account_name))

def validate_account_types(company):
	account_types_for_ledger = ["Cost of Goods Sold", "Depreciation", "Fixed Asset", "Payable", "Receivable", "Stock Adjustment"]

	for account_type in account_types_for_ledger:
		if not frappe.db.get_value("Account",
			{"company": company, "account_type": account_type, "is_group": 0}):

			frappe.throw(_("Please identify / create {0} Account (Ledger)").format(_(account_type)))

	account_types_for_group = ["Bank", "Cash", "Stock"]

	for account_type in account_types_for_group:
		if not frappe.db.get_value("Account",
			{"company": company, "account_type": account_type, "is_group": 1}):

			frappe.throw(_("Please identify / create {0} Account (Group)").format(account_type))

def validate_accounts(company):
	for account in frappe.db.sql("""select name from tabAccount
		where company=%s and ifnull(parent_account, '') != '' order by lft, rgt""", company, as_dict=1):
			frappe.get_doc("Account", account.name).validate()


@frappe.whitelist()
def add_star(company):
	stars_given_by = frappe.db.get_value("Company", company, "stars_given_by")

	if isinstance(stars_given_by, string_types):
		stars_given_by = json.loads(stars_given_by)

	if not stars_given_by:
		stars_given_by = []

	if frappe.session.user not in stars_given_by:
		stars_given_by.append(frappe.session.user)

	stars = len(stars_given_by)
	frappe.db.set_value("Company", company, "stars", stars)
	frappe.db.set_value("Company", company, "stars_given_by", json.dumps(stars_given_by))

	return stars

def get_home_page(user):
	return "/all_charts"

@frappe.whitelist()
def create_new_chart(country):
	frappe.local.flags.ignore_chart_of_accounts = True

	company = create_company(country + " - Chart of Accounts", country, "INR", None)

	frappe.local.flags.ignore_chart_of_accounts = False

	return company

@frappe.whitelist(allow_guest=True)
def get_countries():
	return [d.name for d in frappe.get_all("Country")]

@frappe.whitelist()
def export_submitted_coa(country=None, chart=None):
	"""
		Make charts tree and export submitted charts as .json files
		to public/files/submitted_charts
		:param country: Country name is optional
	"""

	path = os.path.join(os.path.abspath(frappe.get_site_path()), "public", "files", "submitted_charts")
	frappe.create_folder(path)

	filters = {"submitted": 1}
	if country:
		filters.update({"country": country})
	if chart:
		filters.update({"name": chart})

	company_for_submitted_charts = frappe.get_all("Company", filters,
		["name", "country", "chart_of_accounts_name"])

	for company in company_for_submitted_charts:
		account_tree = get_account_tree_from_existing_company(company.name)
		write_chart_to_file(account_tree, company, path)

	make_tarfile(path, company_for_submitted_charts[0].chart_of_accounts_name or chart)

@frappe.whitelist()
def edit_chart(chart):
	frappe.cache().hset("edit_chart", frappe.session.user, True)

def disable_submitted(company):
	if frappe.cache().hget("edit_chart", frappe.session.user):
		frappe.db.set_value("Company", company, "submitted", 0)
		frappe.cache().hset("edit_chart", frappe.session.user, False)

def write_chart_to_file(account_tree, company, path):
	"""
		Write chart to json file and make tar file for all charts
	"""
	chart = {}
	chart["name"] = company.chart_of_accounts_name or company.name
	if company.domain:
		chart["domain"] = company.domain
	chart["country_code"] = frappe.db.get_value("Country", company.country, "code")
	chart["tree"] = account_tree

	fpath = os.path.join(path, (company.chart_of_accounts_name or company.name) + ".json")
	if not os.path.exists(fpath):
		with open(os.path.join(path, (company.chart_of_accounts_name or company.name) + ".json"), "w") as f:
			f.write(json.dumps(chart, indent=4, sort_keys=True))

def make_tarfile(path, fname=None):
	if not fname:
		fname = "charts"
		source_path = path
	else:
		source_path = os.path.join(path, fname + ".json").encode('utf-8')

	target_path = os.path.join(path, fname + ".tar.gz").encode('utf-8')

	source_path = frappe.safe_decode(source_path)
	target_path = frappe.safe_decode(target_path)

	with tarfile.open(target_path, "w:gz", encoding="utf-8") as tar:
		tar.add(source_path, arcname=os.path.basename(source_path))

@frappe.whitelist(allow_guest=True)
def init_details(company):
	out = frappe.cache().hget("init_details", frappe.session.user)

	if not out or company!=out['company']['name']:
		company_details = frappe.db.get_all("Company", {"name": company}, ["chart_of_accounts_name,\
			name, submitted, forked, included_in_erpnext, domain"])[0]
		domains = [d.name for d in frappe.db.get_all("Domain")]

		out = {
			"accounts_meta": frappe.get_meta('Account'),
			"company": company_details or {},
			"domains": domains
		}
		frappe.cache().hset("init_details", frappe.session.user, out)

	return out