#!/usr/bin/env python3
"""Offline, source-preserving virtual-product risk lookup. Python 3.10+."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import unicodedata
from collections import Counter, OrderedDict
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handling  # noqa: E402  运营处置建议（纯本地规则表）
import disguise  # noqa: E402  伪装名识别（拼音归一化）

DEFAULT_DB = Path(__file__).resolve().parents[1] / 'assets' / 'risk-names.json'
CAUTION = '来源线索未外部核验；命中不是违规结论，未命中也不代表安全或已有转售授权。'
WELCOME = '老杨欢迎您使用本雷达，有任何建议与我联系：v:yhslgg'
LIST_FIELDS = ('variants', 'companies', 'labels', 'registrations')


def normalize(text):
    text = unicodedata.normalize('NFKC', str(text))
    text = re.sub(r'[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]', '', text)
    return re.sub(r'\s+', ' ', text).strip().casefold()


def unique(values):
    return list(dict.fromkeys(v for v in values if v))


def load_db(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('词库必须是 schema_version=1 的 JSON 对象')
    records = data.get('records')
    if not isinstance(records, list):
        raise ValueError('records 必须是数组')
    seen = set()
    for r in records:
        if not isinstance(r, dict) or not isinstance(r.get('name'), str) or not normalize(r['name']):
            raise ValueError('每条记录必须有非空字符串 name')
        rid = r.get('id')
        if not isinstance(rid, str) or not rid or rid in seen:
            raise ValueError('每条记录的 id 必须非空且唯一')
        seen.add(rid)
        for field in LIST_FIELDS:
            if not isinstance(r.get(field, []), list) or any(not isinstance(x, str) for x in r.get(field, [])):
                raise ValueError(f'{rid}: {field} 必须是字符串数组')
        if not isinstance(r.get('sources', []), list) or any(not isinstance(s, dict) for s in r.get('sources', [])):
            raise ValueError(f'{rid}: sources 必须是对象数组')
    return data


QUERY_FIELDS = ('id', 'name', 'category', 'object_type', 'companies', 'labels', 'registrations')


_DISGUISE_INDEX = {}


def disguise_index(db):
    key = id(db)
    if key not in _DISGUISE_INDEX:
        _DISGUISE_INDEX[key] = disguise.index_records(
            [r for r in db['records'] if r.get('object_type') != '店铺' and r.get('category') != '店铺来源提醒'])
    return _DISGUISE_INDEX[key]


def query_one(db, item, limit=10):
    q = normalize(item)
    if not q:
        raise ValueError('查询名称不能为空')
    matches = []
    for r in db['records']:
        if r.get('object_type') == '店铺' or r.get('category') == '店铺来源提醒':
            continue
        names = unique([r['name'], *r.get('variants', [])])
        companies = r.get('companies', [])
        mode, rank, term = '', 99, ''
        for name in names:
            if q == normalize(name):
                mode, rank, term = 'exact_name', 0, name
                break
        if not mode:
            for company in companies:
                if q == normalize(company):
                    mode, rank, term = 'exact_company', 1, company
                    break
        if not mode and len(q) >= 3:
            for candidate in [*names, *companies]:
                n = normalize(candidate)
                if len(n) >= 3 and (q in n or n in q):
                    mode, rank, term = 'partial_candidate', 2, candidate
                    break
        if mode:
            projected = {k: r[k] for k in QUERY_FIELDS if k in r}
            matches.append((rank, r['name'], dict(projected, match_type=mode, matched_term=term)))
    found = {m[2]['id'] for m in matches}
    for r, term in disguise.find_disguised(item, disguise_index(db)):
        if r['id'] in found:
            continue
        projected = {k: r[k] for k in QUERY_FIELDS if k in r}
        matches.append((3, r['name'], dict(projected, match_type='disguised_candidate', matched_term=term)))
    matches.sort(key=lambda x: (x[0], normalize(x[1]), x[2]['id']))
    shown = matches[:limit]
    top = matches[0][0] if matches else 99
    status = 'matched' if top < 2 else 'candidates' if top == 2 else 'disguised' if top == 3 else 'no_match'
    guidance = ('发现名称或明确公司关联，核对实际内容和转售授权。' if status == 'matched'
                else '发现部分匹配候选，先确认是否为同一主体或作品。' if status == 'candidates'
                else '输入疑似为某个风险名称的伪装写法（谐音、拼音或拆写），先按正写名称复查。' if status == 'disguised'
                else '本地词库未收录该名称，仍需核对实际内容、来源及授权。')
    best = shown[0][2] if shown else None
    return dict(query=item, status=status, total_matches=len(matches), truncated=len(matches)>len(shown),
                matches=[x[2] for x in shown], guidance=guidance, handling=handling.advise(status, best))


def safe_sheet_cell(v):
    # Prevent spreadsheet formula injection in CSV and XLSX, including prefixed whitespace.
    return "'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v


def md(text):
    return str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\r', ' ').replace('\n', '<br>')


def query_markdown(report):
    lines = ['# 选品名单查询', '', f"词库日期：{md(report['updated_at'])}", '', CAUTION]
    modes = {'exact_name':'名称精确命中', 'exact_company':'公司精确关联', 'partial_candidate':'部分匹配候选', 'disguised_candidate':'疑似伪装命中'}
    for result in report['results']:
        lines += ['', '## '+md(result['query']), '', result['guidance'], '',
                  f"共 {result['total_matches']} 条，显示 {len(result['matches'])} 条。"]
        for r in result['matches']:
            lines += ['', f"- {md(r['name'])}：{modes[r['match_type']]}" + (f"（输入对应 {md(r.get('matched_term',''))}）" if r['match_type']=='disguised_candidate' else ''),
                      f"  - 领域：{md(r.get('category', '待分类'))}；公司：{md('；'.join(r.get('companies', []))) or '未列示'}",
                      f"  - 风险标签：{md('；'.join(r.get('labels', []))) or '未列示'}"]
        if result.get('handling'):
            lines += ['', '### 运营处置建议', '', handling.markdown(result['handling'])]
    return '\n'.join(lines)+'\n'


class TableParser(HTMLParser):
    """Static tables only; retain blank cells and basic row/column spans."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables=[]; self.depth=0; self.rows=None; self.row=None; self.cell=None; self.text=[]
        self.skip=0

    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'):
            self.skip+=1
        if tag=='table':
            self.depth+=1
            if self.depth==1:self.rows=[]
        elif self.depth==1 and tag=='tr':self.row=[]
        elif self.depth==1 and tag in ('td','th'):
            self.cell=dict(attrs);self.text=[]
        elif self.cell is not None and tag=='br':self.text.append('\n')

    def handle_data(self, data):
        if self.cell is not None and not self.skip:self.text.append(data)

    def handle_endtag(self, tag):
        if tag in ('script','style'):
            self.skip=max(0,self.skip-1)
        if self.depth==1 and tag in ('td','th') and self.cell is not None:
            if self.row is None:self.row=[]
            self.row.append((''.join(self.text),int(self.cell.get('rowspan','1')),int(self.cell.get('colspan','1'))))
            self.cell=None
        elif self.depth==1 and tag=='tr' and self.row is not None:
            self.rows.append(self.row);self.row=None
        elif tag=='table':
            if self.depth==1:
                self.tables.append(self.expand(self.rows or []));self.rows=None
            self.depth=max(0,self.depth-1)

    @staticmethod
    def expand(rows):
        occupied={}; result=[]
        for ri,row in enumerate(rows):
            ci=0
            for text,rs,cs in row:
                if not (1<=rs<=1000 and 1<=cs<=1000):raise ValueError('HTML 合并范围异常')
                while (ri,ci) in occupied:ci+=1
                for rr in range(ri,ri+rs):
                    for cc in range(ci,ci+cs):occupied[rr,cc]=text if (rr,cc)==(ri,ci) else ''
                ci+=cs
        width=max((c+1 for r,c in occupied),default=0)
        height=max(len(rows),max((r+1 for r,c in occupied),default=0))
        if width*height>2_000_000:raise ValueError('HTML 表格过大，请先拆分')
        for ri in range(height):result.append([occupied.get((ri,ci),'') for ci in range(width)])
        return result


def excel_module():
    try:
        import openpyxl
        return openpyxl
    except ImportError as e:
        raise ValueError('Excel 功能需安装：python3 -m pip install -r requirements-excel.txt') from e


def source_tables(filename):
    path=Path(filename)
    if path.is_dir():path=path/'index.html'
    ext=path.suffix.lower()
    if ext in ('.csv','.tsv'):
        with path.open(encoding='utf-8-sig',newline='') as f:
            return [(path.name,'CSV' if ext=='.csv' else 'TSV',list(csv.reader(f,delimiter=',' if ext=='.csv' else '\t')))]
    if ext in ('.html','.htm'):
        p=TableParser();p.feed(path.read_text(encoding='utf-8-sig'))
        return [(path.name,f'表格{i}',rows) for i,rows in enumerate(p.tables,1)]
    if ext in ('.xlsx','.xlsm'):
        w=excel_module().load_workbook(path,read_only=True,data_only=False)
        try:
            def value(v):return v.isoformat() if isinstance(v,(dt.date,dt.datetime)) else str(v) if v is not None else ''
            return [(path.name,s.title,[[value(v) for v in r] for r in s.iter_rows(values_only=True)]) for s in w]
        finally:w.close()
    raise ValueError('支持 CSV、TSV、XLSX、XLSM 和静态 HTML 表格')


def column_index(headers, selector):
    if selector.startswith('#') and selector[1:].isdigit():
        n=int(selector[1:])-1
        if 0<=n<len(headers):return n
        raise ValueError(f'列号超出范围：{selector}')
    indices=[i for i,h in enumerate(headers) if h.strip()==selector]
    if len(indices)!=1:raise ValueError(f'标题必须存在且唯一：{selector}；重复或空标题请用 #列号')
    return indices[0]


def cell_col(i):
    s='';i+=1
    while i:i,r=divmod(i-1,26);s=chr(65+r)+s
    return s


def merge_records(records):
    groups=OrderedDict()
    used_ids=set()
    for incoming in records:
        key=normalize(incoming['name'])
        if not key:continue
        r=json.loads(json.dumps(incoming,ensure_ascii=False))
        if key not in groups:
            if not r.get('id') or r['id'] in used_ids:
                number=1
                while f'BL{number:04}' in used_ids:number+=1
                r['id']=f'BL{number:04}'
            used_ids.add(r['id']);groups[key]=r
        old=groups[key]
        for f in LIST_FIELDS:
            old[f]=unique([*old.get(f,[]),*r.get(f,[]),*([r['name']] if f=='variants' else [])])
        # Preserve conflicting supplied categories, as well as every distinct source.
        categories=unique([*old.get('category_variants',[]),old.get('category','待分类'),*r.get('category_variants',[]),r.get('category','待分类')])
        old['category_variants']=categories
        if old.get('category','待分类')=='待分类' and r.get('category','待分类')!='待分类':old['category']=r['category']
        sources={json.dumps(s,ensure_ascii=False,sort_keys=True):s for s in [*old.get('sources',[]),*r.get('sources',[])]}
        old['sources']=list(sources.values())
    return list(groups.values())


def build_database(args):
    records=[]; warnings=[]; available=0
    for old in args.merge:records.extend(load_db(old)['records'])
    for filename in args.input:
        for document,sheet,rows in source_tables(filename):
            if args.sheet and sheet!=args.sheet:continue
            if len(rows)<args.header_row:continue
            headers=rows[args.header_row-1]
            try:ni=column_index(headers,args.name_column)
            except ValueError as e:
                if args.sheet:raise
                warnings.append(f'{document} / {sheet}: {e}');continue
            columns={}
            for attr in ('category','company','label','registration','object_type'):
                selector=getattr(args,attr+'_column')
                if selector:columns[attr]=column_index(headers,selector)
            available+=1
            for rowno,row in enumerate(rows[args.header_row:],args.header_row+1):
                value=lambda idx:str(row[idx]).strip() if idx<len(row) and row[idx] is not None else ''
                name=value(ni)
                if not name:continue
                vals={attr:value(ci) for attr,ci in columns.items()}
                source=dict(document=document,sheet=sheet,cell=f'{cell_col(ni)}{rowno}',original_name=name,
                            label=vals.get('label',''),company=vals.get('company',''),registration=vals.get('registration',''))
                records.append(dict(name=name,variants=[name],category=vals.get('category') or '待分类',
                                    object_type=vals.get('object_type') or '名称线索',companies=unique([vals.get('company','')]),
                                    labels=unique([vals.get('label','')]),registrations=unique([vals.get('registration','')]),
                                    verification='unverified',risk_note='用户导入来源记录，未外部核验',sources=[source]))
    if not available:raise ValueError('没有找到可用的表格和名称列；请先运行 inspect')
    return dict(schema_version=1,updated_at=args.as_of or dt.date.today().isoformat(),description='用户附件导入；风险标签未外部核验',records=merge_records(records),import_warnings=warnings)


def candidate_items(args):
    items=list(args.item)
    if args.input:
        p=Path(args.input)
        if p.suffix.lower() in ('.csv','.tsv'):
            tables=source_tables(p);rows=tables[0][2]
            if not rows:raise ValueError('候选文件为空')
            ci=column_index(rows[0],args.field) if args.field else 0
            items.extend(r[ci] for r in rows[1:] if ci<len(r))
        else:items.extend(p.read_text(encoding='utf-8-sig').splitlines())
    items=[str(x).strip() for x in items if normalize(x)]
    if not items:raise ValueError('至少给出一个 --item 或非空 --input')
    return items


EXPORT_HEADERS=['编号','名称','领域','材料对象','关联公司','风险标签','登记号','核验状态','风险线索','来源']


def export_rows(db):
    for r in db['records']:
        refs=[' / '.join(str(s.get(k,'')) for k in ('document','sheet','cell')) for s in r.get('sources',[])]
        yield [r['id'],r['name'],r.get('category','待分类'),r.get('object_type','名称线索'),
               '；'.join(r.get('companies',[])),'；'.join(r.get('labels',[])),'；'.join(r.get('registrations',[])),
               '未外部核验' if r.get('verification','unverified')=='unverified' else r['verification'],r.get('risk_note',''), '\n'.join(refs)]


def export_markdown(db):
    groups={}
    for r in export_rows(db):groups.setdefault(r[2],[]).append(r)
    lines=['# 虚拟电商选品风险线索','',f"词库日期：{md(db.get('updated_at','未提供'))}。名称数：{len(db['records'])}。",'',CAUTION]
    for category,rows in sorted(groups.items()):
        lines+=['','## '+md(category),'','| 编号 | 名称 | 公司 | 风险标签 | 来源 |','| --- | --- | --- | --- | --- |']
        lines+=['| '+' | '.join(md(r[i]) for i in (0,1,4,5,9))+' |' for r in rows]
    return '\n'.join(lines)+'\n'


def export_xlsx(db,path):
    ox=excel_module();from openpyxl.styles import Font,PatternFill,Alignment
    from openpyxl.worksheet.table import Table,TableStyleInfo
    w=ox.Workbook();s=w.active;s.title='名单总表';s.append(EXPORT_HEADERS)
    fragments=w.create_sheet('长字段全文');fragments.append(['条目编号','字段名','分段序号','总分段数','原文片段'])
    row_counts={}
    def text_row(ws,values):
        row_no=row_counts.get(ws.title,1)+1
        ws.append(values)
        row_counts[ws.title]=row_no
        for ci,value in enumerate(values,1):
            if isinstance(value,str):ws.cell(row_no,ci).data_type='s'

    def append_full(ws,row,labels):
        converted=[]
        for i,value in enumerate(row):
            if len(str(value))>30000:
                chunks=[value[j:j+30000] for j in range(0,len(value),30000)]
                field=f'{ws.title} / {labels[i]}'
                for number,chunk in enumerate(chunks,1):text_row(fragments,[row[0],field,number,len(chunks),chunk])
                value=f'完整字段见长字段全文：{row[0]} / {field}'
            converted.append(value)
        text_row(ws,converted)
    for row in export_rows(db):append_full(s,row,EXPORT_HEADERS)
    source=w.create_sheet('来源明细');source.append(['编号','来源文档','原表','原位置','原名称','原标签','原公司','原登记号'])
    for r in db['records']:
        for ss in r.get('sources',[]):
            append_full(source,[str(v) for v in [r['id'],*[ss.get(k,'') for k in ('document','sheet','cell','original_name','label','company','registration')]]],
                        ['编号','来源文档','原表','原位置','原名称','原标签','原公司','原登记号'])
    for i,ws in enumerate(w):
        ws.sheet_view.showGridLines=False;ws.freeze_panes='C2' if ws==s else 'A2'
        for c in ws[1]:c.font=Font(name='Arial',bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='243B53')
        for row in ws.iter_rows(min_row=2):
            for c in row:c.alignment=Alignment(vertical='top',wrap_text=True)
        for column in ws.columns:ws.column_dimensions[column[0].column_letter].width=18 if column[0].column==1 else 44
        if ws.max_row>1:
            table=Table(displayName=f'RiskRecords{i}',ref=ws.dimensions)
            table.tableStyleInfo=TableStyleInfo(name='TableStyleMedium2',showRowStripes=True)
            ws.add_table(table)
    w.save(path)


def emit(text,filename=None,overwrite=False,bom=False):
    if not filename:
        print(text,end='' if text.endswith('\n') else '\n');return
    path=Path(filename);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w' if overwrite else 'x',encoding='utf-8-sig' if bom else 'utf-8',newline='') as f:f.write(text)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('welcome')
    p=sub.add_parser('lint');p.add_argument('--text',action='append',default=[]);p.add_argument('--input');p.add_argument('--database',default=str(DEFAULT_DB))
    p.add_argument('--format',choices=['json','markdown'],default='json');p.add_argument('--output');p.add_argument('--overwrite',action='store_true')
    for command in ('query','stats','export'):
        p=sub.add_parser(command);p.add_argument('--database',default=str(DEFAULT_DB))
        if command=='query':
            p.add_argument('--item',action='append',default=[]);p.add_argument('--input');p.add_argument('--field')
            p.add_argument('--limit',type=int,default=10);p.add_argument('--format',choices=['json','markdown'],default='json')
        if command=='export':p.add_argument('--format',choices=['json','csv','markdown','xlsx'],required=True)
        if command!='stats':p.add_argument('--output');p.add_argument('--overwrite',action='store_true')
    p=sub.add_parser('inspect');p.add_argument('--input',required=True)
    p=sub.add_parser('build');p.add_argument('--input',action='append',required=True);p.add_argument('--sheet')
    p.add_argument('--header-row',type=int,default=1);p.add_argument('--name-column',required=True)
    for c in ('category','company','label','registration','object-type'):p.add_argument('--'+c+'-column')
    p.add_argument('--merge',action='append',default=[]);p.add_argument('--output',required=True)
    p.add_argument('--as-of');p.add_argument('--overwrite',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='welcome':
        print(WELCOME);return 0
    try:
        if args.command=='inspect':
            data=[dict(document=doc,sheet=sheet,rows=len(rows),nonempty_rows=sum(any(str(v).strip() for v in r) for r in rows),preview=rows[:6]) for doc,sheet,rows in source_tables(args.input)]
            emit(json.dumps(data,ensure_ascii=False,indent=2));return 0
        if args.command=='build':
            if args.header_row<1:raise ValueError('--header-row 必须大于零')
            if args.as_of:dt.date.fromisoformat(args.as_of)
            data=build_database(args);emit(json.dumps(data,ensure_ascii=False,indent=2)+'\n',args.output,args.overwrite)
            print(f'已导入 {len(data["records"])} 个名称。',file=sys.stderr)
            for warning in data['import_warnings']:print('跳过：'+warning,file=sys.stderr)
            return 0
        db=load_db(args.database)
        if args.command=='lint':
            import lint_listing
            texts=list(args.text)
            if args.input:texts+=[t for t in Path(args.input).read_text(encoding='utf-8-sig').splitlines() if t.strip()]
            if not texts:raise ValueError('请用 --text 或 --input 提供标题或详情文本')
            report=dict(updated_at=db.get('updated_at','未提供'),notice=lint_listing.NOTICE,results=[lint_listing.lint(db,t) for t in texts])
            text=lint_listing.markdown(report) if args.format=='markdown' else json.dumps(report,ensure_ascii=False,indent=2)+'\n'
            emit(text,args.output,args.overwrite);return 0
        if args.command=='stats':
            print(WELCOME,file=sys.stderr)
            emit(json.dumps(dict(updated_at=db.get('updated_at','未提供'),records=len(db['records']),categories=dict(Counter(r.get('category','待分类') for r in db['records'])),companies=len({c for r in db['records'] for c in r.get('companies',[])}),notice=CAUTION),ensure_ascii=False,indent=2));return 0
        if args.command=='query':
            if args.limit<1:raise ValueError('--limit 必须大于零')
            report=dict(updated_at=db.get('updated_at','未提供'),notice=CAUTION,results=[query_one(db,item,args.limit) for item in candidate_items(args)])
            text=query_markdown(report) if args.format=='markdown' else json.dumps(report,ensure_ascii=False,indent=2)+'\n'
            emit(text,args.output,args.overwrite);return 0
        if args.format=='xlsx':
            if not args.output:raise ValueError('Excel 导出需要 --output')
            if Path(args.output).exists() and not args.overwrite:raise FileExistsError(args.output)
            Path(args.output).parent.mkdir(parents=True,exist_ok=True);export_xlsx(db,args.output);return 0
        if args.format=='csv':
            buffer=io.StringIO(newline='');writer=csv.writer(buffer);writer.writerow(EXPORT_HEADERS)
            writer.writerows([[safe_sheet_cell(v) for v in row] for row in export_rows(db)]);text=buffer.getvalue()
        else:text=export_markdown(db) if args.format=='markdown' else json.dumps(db,ensure_ascii=False,indent=2)+'\n'
        emit(text,args.output,args.overwrite,bom=args.format=='csv');return 0
    except (ValueError,OSError,KeyError,TypeError) as e:
        print(f'错误：{e}',file=sys.stderr);return 2


if __name__=='__main__':sys.exit(main())
