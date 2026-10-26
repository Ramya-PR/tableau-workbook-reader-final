"""Test data builders: a synthetic workbook, .twbx archives and a real .hyper file.

The synthetic workbook is modelled on the structure of real Tableau files
(including the ``_.fcp.ObjectModelTableType.true...column`` element variant,
the ``Parameters`` datasource, sets as ``<group user:ui-builder='filter-group'>``,
published (sqlproxy) and live datasources).
"""

from __future__ import annotations

import datetime as dt
import decimal
import zipfile
from pathlib import Path

SECRET_USER = "secret_user_42"
SECRET_PASSWORD = "hunter2-do-not-leak"

SAMPLE_TWB = f"""<?xml version='1.0' encoding='utf-8' ?>
<workbook original-version='18.1' source-build='2024.1.0 (20241.24.0312.1111)' source-platform='win' version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <datasources>
    <datasource hasconnection='false' inline='true' name='Parameters' version='18.1'>
      <aliases enabled='yes' />
      <column caption='Top N' datatype='integer' name='[Parameter 1]' param-domain-type='range' role='measure' type='quantitative' value='10'>
        <calculation class='tableau' formula='10' />
        <range max='50' min='1' />
      </column>
    </datasource>

    <datasource caption='Sample Sales' inline='true' name='federated.abc123' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='Sample Sales' name='hyper.xyz'>
            <connection class='hyper' dbname='Data/Extracts/sales.hyper' username='{SECRET_USER}' password='{SECRET_PASSWORD}' />
          </named-connection>
        </named-connections>
        <relation connection='hyper.xyz' name='Extract' table='[Extract].[Extract]' type='table' />
        <metadata-records>
          <metadata-record class='column'><remote-name>Region</remote-name><local-name>[Region]</local-name><parent-name>[Extract]</parent-name><local-type>string</local-type></metadata-record>
          <metadata-record class='column'><remote-name>Sales</remote-name><local-name>[Sales]</local-name><parent-name>[Extract]</parent-name><local-type>real</local-type></metadata-record>
          <metadata-record class='column'><remote-name>Order Date</remote-name><local-name>[Order Date]</local-name><parent-name>[Extract]</parent-name><local-type>date</local-type></metadata-record>
          <metadata-record class='column'><remote-name>Qty</remote-name><local-name>[Qty]</local-name><parent-name>[Extract]</parent-name><local-type>integer</local-type></metadata-record>
          <metadata-record class='column'><remote-name>Notes</remote-name><local-name>[Notes]</local-name><parent-name>[Extract]</parent-name><local-type>string</local-type></metadata-record>
        </metadata-records>
      </connection>
      <aliases enabled='yes' />
      <column datatype='string' hidden='true' name='[Notes]' role='dimension' type='nominal' />
      <column caption='Profit' datatype='real' name='[Calculation_1001]' role='measure' type='quantitative'>
        <calculation class='tableau' formula='[Sales] * 0.2' />
      </column>
      <column caption='Profit vs Target' datatype='real' name='[Calculation_1002]' role='measure' type='quantitative'>
        <calculation class='tableau' formula='[Calculation_1001] - [Parameters].[Parameter 1]' />
      </column>
      <_.fcp.ObjectModelTableType.true...column caption='Region Group' datatype='string' name='[Region (group)]' role='dimension' type='nominal'>
        <calculation class='categorical-bin' column='[Region]' new-bin='true' />
      </_.fcp.ObjectModelTableType.true...column>
      <column caption='Sales (bin)' datatype='integer' name='[Sales (bin)]' role='dimension' type='ordinal'>
        <calculation class='bin' decimals='0' formula='[Sales]' peg='0' size='100' />
      </column>
      <group caption='West Set' name='[West Set]' name-style='unqualified' user:ui-builder='filter-group'>
        <groupfilter function='member' level='[none:Region:nk]' member='&quot;West&quot;' />
      </group>
      <extract count='-1' enabled='true' units='records'>
        <connection class='hyper' dbname='C:/Users/someone/AppData/Local/Temp/TableauTemp/sales.hyper' schema='Extract' tablename='Extract'>
          <relation name='Extract' table='[Extract].[Extract]' type='table' />
        </connection>
      </extract>
    </datasource>

    <datasource caption='Shared Customers' inline='true' name='sqlproxy.0pub' version='18.1'>
      <repository-location derived-from='https://tableau.example.com/t/mysite/datasources/SharedCustomers' id='SharedCustomers' path='/t/mysite/datasources' revision='1.0' site='mysite' />
      <connection channel='https' class='sqlproxy' dbname='SharedCustomers' port='443' server='tableau.example.com'>
        <relation name='sqlproxy' table='[sqlproxy]' type='table' />
      </connection>
      <column datatype='string' name='[Customer]' role='dimension' type='nominal' />
    </datasource>

    <datasource caption='Orders DB' inline='true' name='federated.live1' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='db.example.com' name='postgres.1'>
            <connection class='postgres' dbname='orders' port='5432' server='db.example.com' username='{SECRET_USER}' password='{SECRET_PASSWORD}' />
          </named-connection>
        </named-connections>
        <relation connection='postgres.1' name='Custom SQL Query' type='text'>SELECT * FROM orders WHERE region = 'W'</relation>
        <metadata-records>
          <metadata-record class='column'><remote-name>order_id</remote-name><local-name>[order_id]</local-name><parent-name>[Custom SQL Query]</parent-name><local-type>integer</local-type></metadata-record>
          <metadata-record class='column'><remote-name>region</remote-name><local-name>[region]</local-name><parent-name>[Custom SQL Query]</parent-name><local-type>string</local-type></metadata-record>
        </metadata-records>
      </connection>
    </datasource>
  </datasources>

  <worksheets>
    <worksheet name='Sales by Region'>
      <table>
        <view>
          <datasources>
            <datasource caption='Sample Sales' name='federated.abc123' />
          </datasources>
          <datasource-dependencies datasource='federated.abc123'>
            <column datatype='string' name='[Region]' role='dimension' type='nominal' />
            <column datatype='real' name='[Sales]' role='measure' type='quantitative' />
            <column caption='Profit' datatype='real' name='[Calculation_1001]' role='measure' type='quantitative'>
              <calculation class='tableau' formula='[Sales] * 0.2' />
            </column>
            <column-instance column='[Region]' derivation='None' name='[none:Region:nk]' pivot='key' type='nominal' />
            <column-instance column='[Sales]' derivation='Sum' name='[sum:Sales:qk]' pivot='key' type='quantitative' />
            <column-instance column='[Sales]' derivation='Avg' name='[avg:Sales:qk]' pivot='key' type='quantitative' />
            <column-instance column='[Calculation_1001]' derivation='User' name='[usr:Calculation_1001:qk]' pivot='key' type='quantitative' />
          </datasource-dependencies>
          <datasource-dependencies datasource='Parameters'>
            <column caption='Top N' datatype='integer' name='[Parameter 1]' param-domain-type='range' role='measure' type='quantitative' value='10'>
              <calculation class='tableau' formula='10' />
            </column>
          </datasource-dependencies>
          <filter class='categorical' column='[federated.abc123].[none:Region:nk]'>
            <groupfilter function='member' level='[none:Region:nk]' member='&quot;East&quot;' />
          </filter>
        </view>
        <panes>
          <pane>
            <view><breakdown value='auto' /></view>
            <mark class='Bar' />
          </pane>
        </panes>
        <rows>[federated.abc123].[sum:Sales:qk]</rows>
        <cols>[federated.abc123].[none:Region:nk]</cols>
      </table>
    </worksheet>
    <worksheet name='Detail'>
      <table>
        <view>
          <datasources>
            <datasource caption='Sample Sales' name='federated.abc123' />
          </datasources>
          <datasource-dependencies datasource='federated.abc123'>
            <column datatype='string' name='[Region]' role='dimension' type='nominal' />
            <column-instance column='[Region]' derivation='None' name='[none:Region:nk]' pivot='key' type='nominal' />
          </datasource-dependencies>
        </view>
        <panes><pane><view><breakdown value='auto' /></view><mark class='Text' /></pane></panes>
        <rows>[federated.abc123].[none:Region:nk]</rows>
      </table>
    </worksheet>
  </worksheets>

  <dashboards>
    <dashboard name='Overview'>
      <zones>
        <zone h='100000' id='4' type-v2='layout-basic' w='100000' x='0' y='0'>
          <zone h='50000' id='3' name='Sales by Region' w='100000' x='0' y='0' />
          <zone h='10000' id='5' name='Sales by Region' param='[federated.abc123].[none:Region:nk]' type-v2='filter' w='20000' x='0' y='50000' />
          <zone h='10000' id='6' type-v2='text' w='20000' x='0' y='60000'><formatted-text><run>Hello</run></formatted-text></zone>
        </zone>
      </zones>
    </dashboard>
  </dashboards>

  <actions>
    <action caption='Filter 1' name='[Action1]'>
      <source dashboard='Overview' type='sheet' worksheet='Sales by Region' />
    </action>
  </actions>
</workbook>
""".encode("utf-8")


def make_twbx(path: Path, twb_xml: bytes = SAMPLE_TWB, files: dict[str, bytes] | None = None, twb_name: str = "Sales.twb") -> Path:
    """Write a packaged workbook (.twbx) containing ``twb_xml`` and extra ``files``."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(twb_name, twb_xml)
        for name, data in (files or {}).items():
            zf.writestr(name, data)
    return path


REGIONS = ["East", "West", "Zürich", "North"]


def make_hyper(path: Path, rows: int = 250) -> Path:
    """Create a real .hyper extract with an Extract.Extract table (needs tableauhyperapi)."""
    from tableauhyperapi import (
        Connection,
        CreateMode,
        HyperProcess,
        Inserter,
        SqlType,
        TableDefinition,
        TableName,
        Telemetry,
    )

    table = TableDefinition(
        TableName("Extract", "Extract"),
        [
            TableDefinition.Column("Region", SqlType.text()),
            TableDefinition.Column("Sales", SqlType.double()),
            TableDefinition.Column("Order Date", SqlType.date()),
            TableDefinition.Column("Qty", SqlType.int()),
            TableDefinition.Column("Price", SqlType.numeric(10, 2)),
            TableDefinition.Column("Shipped At", SqlType.timestamp()),
        ],
    )
    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={"log_config": ""}) as hp:
        with Connection(hp.endpoint, str(path), CreateMode.CREATE_AND_REPLACE) as conn:
            conn.catalog.create_schema("Extract")
            conn.catalog.create_table(table)
            with Inserter(conn, table) as ins:
                for i in range(rows):
                    ins.add_row(
                        [
                            None if i == 3 else REGIONS[i % len(REGIONS)],
                            float(i) * 1.5,
                            dt.date(2024, 1, 1 + i % 28),
                            i,
                            decimal.Decimal("1.25") + i,
                            dt.datetime(2024, 3, 1 + i % 28, 13, 45, 30),
                        ]
                    )
                ins.execute()
    return path


def hyper_works() -> bool:
    """True if the Hyper API is importable AND hyperd can actually start here."""
    try:
        from tableauhyperapi import HyperProcess, Telemetry

        with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={"log_config": ""}):
            return True
    except Exception:
        return False
