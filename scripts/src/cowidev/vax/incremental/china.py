import re

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait

from cowidev.utils.clean import clean_count, clean_date, extract_clean_date
from cowidev.utils.web.scraping import get_driver, sel_options
from cowidev.vax.utils.base import CountryVaxBase


class China(CountryVaxBase):
    location: str = "China"
    source_url: str = "http://www.nhc.gov.cn/xcs/yqjzqk/list_gzbd.shtml"
    source_url_complete: str = "http://www.nhc.gov.cn/xcs/s2906/new_list.shtml"
    regex: dict = {
        "title": "新冠病毒疫苗接种情况",
        "date": r"截至(20\d{2})年(\d{1,2})月(\d{1,2})日",
        "total_vaccinations": r"([\d\.]+\s*万)剂次",
    }
    chinese: str = r"[\u4e00-\u9fff]*"
    month_day: str = r"，?(?:\d{2,4}年)?(\d{1,2})月(\d{1,2})[\u4e00-\u9fff]+，?"
    metric_ignore: str = r"(?:\d+亿[\u4e00-\u96f5\u96f7-\u9fff，]{1,5})?"
    metric: str = r"((?:\d+亿零?)?[\d\.]+万)"
    regex_complete: dict = {
        "title": r"国务院(?:联防联控机制|新闻办公室)(20\d{2})年(\d{1,2})月(\d{1,2})日新闻发布会",
        "summary": f"截{chinese}{month_day}{chinese}接种{chinese}{metric_ignore}{metric}剂",
        "fully": f"全程接种{chinese}{metric_ignore}{metric}",
        "vaccinated": f"接种{chinese}总人数{chinese}{metric_ignore}{metric}",
        "boosters": f"加强免疫{chinese}接种{chinese}{metric_ignore}{metric}",
    }
    num_links_complete: int = 3
    timeout: int = 30

    def read(self, last_update: str) -> pd.DataFrame:
        data = []
        options = sel_options(headless=True, firefox=True)
        options.set_capability("pageLoadStrategy", "none")
        with get_driver(options=options, firefox=True, timeout=self.timeout) as driver:
            # Load the page until the list of links is loaded
            driver.get(self.source_url)
            Wait(driver, self.timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".zxxx_list>li>a")))
            driver.execute_script("window.stop();")
            links = self._get_links(driver)
            for link in links:
                data_ = self._parse_data(driver, link)
                if data_["date"] <= last_update:
                    break
                data.append(data_)
            assert data_["date"] <= last_update, "Only read data back to: " + data_["date"]
        return pd.DataFrame(data)

    def _get_links(self, driver):
        elems = driver.find_elements_by_css_selector(".zxxx_list>li>a")
        return [elem.get_property("href") for elem in elems if self.regex["title"] in elem.text]

    def _parse_data(self, driver, url):
        # Load the page until the end of the text is loaded
        driver.get(url)
        Wait(driver, self.timeout).until(EC.url_to_be(url))
        Wait(driver, self.timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#xw_box>.clear")))
        driver.execute_script("window.stop();")
        elem = driver.find_element_by_id("xw_box")
        # Apply regex and get metrics
        return {
            "date": extract_clean_date(elem.text, self.regex["date"], "%Y %m %d"),
            "source_url": url,
            "total_vaccinations": clean_count(re.search(self.regex["total_vaccinations"], elem.text).group(1)),
        }

    def read_complete(self) -> pd.DataFrame:
        data = []
        options = sel_options(headless=True, firefox=True)
        options.set_capability("pageLoadStrategy", "none")
        with get_driver(options=options, firefox=True, timeout=self.timeout) as driver:
            # Load the page until the list of links is loaded
            driver.get(self.source_url_complete)
            Wait(driver, self.timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".zxxx_list>li>a")))
            driver.execute_script("window.stop();")
            links = self._get_links_complete(driver)
            for link in links[: self.num_links_complete]:
                data_ = self._parse_data_complete(driver, link)
                if data_:
                    data.append(data_)
        return pd.DataFrame(data)

    def _get_links_complete(self, driver):
        elems = driver.find_elements_by_css_selector(".zxxx_list>li>a")
        return [elem.get_property("href") for elem in elems if re.search(self.regex_complete["title"], elem.text)]

    def _parse_data_complete(self, driver, url):
        def _clean_count(num_as_str):
            num = float(re.search(r"([\d\.]+)万", num_as_str).group(1)) * 1e4
            num_100m = re.search(r"(\d+)亿零?", num_as_str)
            if num_100m:
                num += float(num_100m.group(1)) * 1e8
            return int(num)

        # Load the page until the end of the text is loaded
        driver.get(url)
        Wait(driver, self.timeout).until(EC.url_to_be(url))
        Wait(driver, self.timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#xw_box>.clear")))
        driver.execute_script("window.stop();")
        elem = driver.find_element_by_id("xw_box")
        # Apply regex
        year = re.search(self.regex_complete["title"], driver.title).group(1)
        summary = re.search(self.regex_complete["summary"], elem.text)
        fully = re.search(self.regex_complete["fully"], elem.text)
        if summary and fully:
            month, day, total_vaccinations = summary.groups()
        else:
            return
        vaccinated = re.search(self.regex_complete["vaccinated"], elem.text)
        boosters = re.search(self.regex_complete["boosters"], elem.text)
        # Get metrics
        return {
            "date": clean_date(f"{year}-{month}-{day}", "%Y-%m-%d"),
            "source_url": url,
            "total_vaccinations": _clean_count(total_vaccinations),
            "people_vaccinated": _clean_count(vaccinated.group(1)) if vaccinated else None,
            "people_fully_vaccinated": _clean_count(fully.group(1)),
            "total_boosters": _clean_count(boosters.group(1)) if boosters else None,
        }

    def pipe_metadata(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.assign(location=self.location)

    def pipe_vaccine(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.assign(vaccine="CanSino, IMBCAMS, KCONVAC, Sinopharm/Beijing, Sinopharm/Wuhan, Sinovac, ZF2001")

    def pipeline(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.pipe(self.pipe_metadata).pipe(self.pipe_vaccine)

    def pipeline_merge(self, df_complete: pd.DataFrame, df_last: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
        df_complete, df_last = df_complete.set_index("date"), df_last.set_index("date")
        msk = df_complete.index.isin(df_last.index)
        # Use total_vaccinations from df_last & df
        dates = df_complete.index[msk]
        df_complete.loc[dates, "total_vaccinations"] = df_last.total_vaccinations[dates]
        for metric in ["people_vaccinated", "people_fully_vaccinated", "total_boosters"]:
            # Avoid clearing previously saved metrics
            dates = df_complete.index[msk & df_complete[metric].isna()]
            df_complete.loc[dates, metric] = df_last.loc[dates, metric]
            for date, m in df_complete[metric].items():
                # Check abnormally decreased metrics
                if date >= df_last.index.max() and m < 0.99 * df_last[metric].max():
                    raise ValueError(f"Abnormally decreased {metric}: {m:.0f} ({date})")
        if not df.empty:
            df = df.set_index("date")
            msk = df.index.isin(df_complete.index)
            # Use total_vaccinations from df_last & df
            dates = df.index[msk]
            df_complete.loc[dates, "total_vaccinations"] = df.total_vaccinations[dates]
            df_complete = pd.concat([df_complete, df[~msk]])
        return df_complete.reset_index()

    def export(self):
        # Read
        df_last = self.load_datafile()
        df = self.read(df_last.date.max())
        df_complete = self.read_complete()
        # Transform
        if not df.empty:
            df = df.pipe(self.pipeline)
        if not df_complete.empty:
            df = df_complete.pipe(self.pipeline).pipe(self.pipeline_merge, df_last, df)
        # Export
        if not df.empty:
            self.export_datafile(df, attach=True)


def main():
    China().export()
