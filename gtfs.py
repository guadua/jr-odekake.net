import re
import requests
from lxml import html
import pandas as pd
from urllib.parse import unquote
from pdb import set_trace
MODE=12
FUNC=0
GUID='7c796482-18da-4ac9-b1c6-dbb4953bef35'
COMPANY_CODE=401
TIME='0603'
STATION_ID = '0800613'

AREA_DATA_JS = 'https://www.jr-odekake.net/route/leaflet/area_data.js'

TIMETABLE_BODY_COLUMNS = ('駅', '着発時刻', '番線')
TIMETABLE_HEADER_NAMES = (
    '\u3000列車詳細情報', # TRAIN_DETAIL
    '列車番号', # TRAIN_NUMBER
    '列車種別', # TRAIN_TYPE
    '列車名',  # TRAIN_NAME
    '運転日',  # TRAIN_SCHEDULE
    '駅'      # STATION
)
IS_TIMETABLE_ROW = -1
TRAIN_DETAIL = 0
TRAIN_NUMBER = 1
TRAIN_TYPE = 2
TRAIN_NAME = 3
TRAIN_SCHEDULE = 4
STATION = 5
STATION_NAME_SEPARATOR = '｜'

class Page():
    def __init__(self, url, xpath):
        print(self.__class__, url)
        r = requests.get(url)
        self.url = url
        self.tree = html.fromstring(r.content)
        self.elements = self.tree.xpath(xpath)

class RoutePage(Page):
    pattern = 'd\["(\d+)"\]\=\{nm\:"(.+)"\,rm\:"(.+)"\,kn'
    def __init__(self, url):
        r = requests.get(url)
        scripts = r.content.decode('utf8').split(';')
        df = pd.DataFrame(scripts)
        df[[1, 2, 3]] = df[0].apply(self.script2arr)
        self.df = df

    def script2arr(self, s):
        pattern = self.pattern
        m = re.match(pattern, s)
        if m is None:
            nm, rm, kn = '', '', ''
        else:
            nm, rm, kn = m.groups()
            rm = rm.encode('utf8').decode('unicode-escape')

        return pd.Series([nm, rm, kn])

class StationPage(Page):
    def __init__(self, url):
        super().__init__(url, '//script[@type="text/javascript"]')
        self.station_name = self.extract_station_name()

        inner_html = self.extract_inner_html()

        self.direction_tables = self.extract_direction_tables(inner_html)

        # 方面情報のリンク先それぞれから時刻表ページのリンク抽出
        links = []
        for table in direction_tables:
            arr = self.extract_dialinks(table)
            for a in arr:
                links.append(a)
        self.dialinks = pd.DataFrame(links)

        self.extract_timetable_and_trips()

    def extract_station_name(self):
        station_name = self.tree.xpath('//title')[0].text.split(STATION_NAME_SEPARATOR)[0]
        print(station_name)
        return station_name

    def extract_inner_html(self):
        '''
        方面情報は別 html
        '''
        elements = self.elements
        scripts = [el.text for el in elements if el.text is not None]
        assert len(scripts) == 1
        script = scripts[0]
        url = script.split('url: \'')[1].split('\'')[0]
        print('inner_html:', url)
        r = requests.get(url)
        return r.content.decode('utf8')
    
    def extract_direction_tables(self, inner_html):
        '''
        inner_html から方面情報の table タグを抽出
        '''
        tree = html.fromstring(inner_html)
        return tree.xpath('//table[@class="ekiTable03 timetable"]')
    
    def extract_dialinks(self, table):
        '''
        時刻表ページへのリンクを抽出
        '''
        route = table.attrib['summary']
        links = table.xpath('tr/td/a')
        arr = []
        for l in links:
            direction = l.text
            href = l.attrib['onclick'].split('\'')[1]
            arr.append((route, direction, href))
        return arr

    def extract_timetable_and_trips(self):
        '''
        !!! 負荷大 !!!

        時刻表のページのリンク
          -> 全出発時間取得
            -> その出発時間ごとの列車詳細情報取得
        '''
        dialinks = self.dialinks
        for index, row in dialinks.iterrows():
            route, direction, url = row

            # 時刻表の全出発時間, その便リンク取得
            page = TimetablePage(route, direction, url)
            for index, row in page.sta_all.iterrows():
                rel_path = row[3]
                trip_url = url.split('mydia.cgi')[0] + rel_path
                dia_page = DiagramPage(trip_url)
            set_trace()

class TimetablePage(Page):
    '''
    駅時刻表

    時 | 分
    6  | 01 
    7  | 00 10 20 30 40 
    ...
    23 | 05    
    '''

    def __init__(self, route, direction, url):
        super().__init__(url, '//table[@id="weekday"]')
        assert len(self.elements) == 1
        rows = self.elements[0].xpath('tr')
        sta_list_by_day = []
        for row in rows:
            sta_by_hour = self.to_sta_by_hour(row)
            if sta_by_hour is not None:
                for sta in sta_by_hour:
                    sta_list_by_day.append(sta)
        self.route = route
        self.direction = direction
        self.sta_all = pd.DataFrame(sta_list_by_day)
    
    def to_sta_by_hour(self, row):
        th_list = row.xpath('th')
        if len(th_list) == 1:
            header = th_list[0].text_content()
        else:
            return None

        if header.isdigit():
            hour = int(header)
        else:
            return None

        minutes = row.xpath('td')
        arr = []
        for m in minutes:
            a_list = m.xpath('a')
            if len(a_list) == 1:
                a = a_list[0]
                minute = a.text_content()
                url = a.attrib['href']
                arr.append((hour, minute, m.text_content(), url))
        return arr

class DiagramPage(Page):
    '''
    列車詳細情報
    '''
    def __init__(self, url):
        super().__init__(url, '//table[@class="timetable"]')
        header, stop_times = self.split_table_to_header_and_stoptimes()
        self.header = header
        self.stop_times = stop_times

    def count_cells(self, tr):
        n_th, n_td = 0, 0

        for cell in tr:
            if cell is None:
                continue
            elif cell.tag == 'th':
                n_th += 1
            elif cell.tag == 'td':
                n_td += 1
        return pd.Series([n_th, n_td])

    def is_timetable_header(self, row):
        '''
        1列目を TIMETABLE_HEADER_NAMES から判定
        '''
        if any([
            (row['n_th'], row['n_td']) == (1, 0), # 0TRAIN_DETAIL
            (row['n_th'], row['n_td']) == (2, 0), # 1TRAIN_NUMBER or 4TRAIN_SCHEDULE
            (row['n_th'], row['n_td']) == (0, 2), # 2TRAIN_NAME
            (row['n_th'], row['n_td']) == (1, 1), # 3TRAIN_TYPE
            (row['n_th'], row['n_td']) == (3, 0)  # 5STATION
            ]): 
            return TIMETABLE_HEADER_NAMES.index(row[0].text)
        else:
            return -1

    def number_of_trip(self, df):
        nrows_train_number = len(df[df['is_timetable_header'] == TRAIN_NUMBER])
        nrows_train_schedule = len(df[df['is_timetable_header'] == TRAIN_SCHEDULE])
        
        assert nrows_train_number == nrows_train_schedule
        return nrows_train_number

    def extract_header(self, row):
        cells = []
        for cell in row[:3]:            
            if cell is None or isinstance(cell, int):
                cells.append(cell)
            else:
                cells.append(cell.text_content())

        return pd.Series(cells)

    def extract_sta_and_std(self, row):
        stop_name = row[0].text
        
        if row[1].xpath('br') == 1:
            sta = row[1].text
            std = row.xpath('br').tail
        else:
            sta = std = row[1].text
    
        platform_code = row[2].text

        return pd.Series([stop_name, sta, std, platform_code])

    def split_table_to_header_and_stoptimes(self):
        time_tables = self.elements
        assert len(time_tables) == 1 # class="timetable" は 1 つ
        time_table = time_tables[0]

        df = pd.DataFrame(time_table.xpath('tr'))

        df[['n_th', 'n_td']] = df.apply(self.count_cells, axis=1)
        df[['is_timetable_header']] = df.apply(self.is_timetable_header, axis=1)

        ntrip = self.number_of_trip(df)

        df_header = df.loc[df['is_timetable_header'] > -1].apply(self.extract_header, axis=1)

        df_stoptimes = df.loc[df['is_timetable_header'] == -1]
        df_stoptimes = df_stoptimes.apply(self.extract_sta_and_std, axis=1)

        return df_header, df_stoptimes

def main():
    all_stations = RoutePage(AREA_DATA_JS).df
    set_trace()

    # 広島駅
    url = 'https://www.jr-odekake.net/eki/timetable?id=0800613'
    # 横川駅
    # url = 'https://www.jr-odekake.net/eki/timetable?id=0800614'
    page = StationPage(url)

    # 広島駅東海道・山陽・九州新幹線　新大阪・東京方面
    url = 'https://mydia.jr-odekake.net/cgi-bin/mydia.cgi?MODE=11&FUNC=0&EKI=%e5%ba%83%e5%b3%b6&SENK=%e6%9d%b1%e6%b5%b7%e9%81%93%e3%83%bb%e5%b1%b1%e9%99%bd%e3%83%bb%e4%b9%9d%e5%b7%9e%e6%96%b0%e5%b9%b9%e7%b7%9a&DIR=%e6%96%b0%e5%a4%a7%e9%98%aa%e3%83%bb%e6%9d%b1%e4%ba%ac%e6%96%b9%e9%9d%a2&DDIV=&CDAY=&DITD=%33%38%36%33%30%30%32%30%30%32%30%30%30%2c%33%38%36%33%30%30%32%30%30%32%30%31%30&COMPANY_CODE=4&DATE=20210313'

    # 広島駅 東海道新幹線
    url0 = 'https://mydia.jr-odekake.net/cgi-bin/mydia.cgi?MODE=12&FUNC=0&GUID=7c796482-18da-4ac9-b1c6-dbb4953bef35&COMPANY_CODE=401&TIDX=%31%30%31%32%37%39%37&TIME=0603' 

    # 横川駅 可部線 安芸亀山
    url1 = 'https://mydia.jr-odekake.net/cgi-bin/mydia.cgi?MODE=12&FUNC=0&GUID=81c69ed7-a3e9-4fe0-af1e-f6ef2591e5d8&COMPANY_CODE=401&TIDX=%36%35%34%30&TIME=0600'

    page0 = DiagramPage(url0)
    page1 = DiagramPage(url1)
    set_trace()

if __name__ == '__main__':
    main()