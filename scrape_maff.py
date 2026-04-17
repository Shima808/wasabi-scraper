"""
農水省「うちの郷土料理」スクレイパー
https://www.maff.go.jp/j/keikaku/syokubunka/k_ryouri/search_menu/

必要なパッケージ:
  pip install requests beautifulsoup4 supabase python-dotenv

使い方:
  python scrape_maff.py                   # 通常スクレイプ（チェックポイント対応）
  python scrape_maff.py --ingredients-only # 既存レシピの食材のみ再取得・再INSERT
"""

import os
import sys
import time
import logging
from urllib.parse import urljoin
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from supabase import create_client

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

BASE_URL = 'https://www.maff.go.jp'
AREA_INDEX_URL = f'{BASE_URL}/j/keikaku/syokubunka/k_ryouri/search_menu/area/index.html'
HEADERS = {'User-Agent': 'wasabi-api-scraper/1.0 (research; contact via github)'}
SLEEP_SEC = 1.5  # サーバー負荷を下げるため各リクエスト間に待機
CHECKPOINT_FILE = 'maff_done_urls.txt'        # 処理済み URL を記録して再開に使う
INGR_CHECKPOINT_FILE = 'maff_ingr_done_urls.txt'  # 食材 INSERT 済み URL を記録

# 都道府県 → 地方マッピング
REGION_MAP = {
    '北海道': '北海道',
    '青森県': '東北', '岩手県': '東北', '宮城県': '東北',
    '秋田県': '東北', '山形県': '東北', '福島県': '東北',
    '茨城県': '関東', '栃木県': '関東', '群馬県': '関東',
    '埼玉県': '関東', '千葉県': '関東', '東京都': '関東', '神奈川県': '関東',
    '新潟県': '中部', '富山県': '中部', '石川県': '中部', '福井県': '中部',
    '山梨県': '中部', '長野県': '中部', '岐阜県': '中部', '静岡県': '中部', '愛知県': '中部',
    '三重県': '近畿', '滋賀県': '近畿', '京都府': '近畿', '大阪府': '近畿',
    '兵庫県': '近畿', '奈良県': '近畿', '和歌山県': '近畿',
    '鳥取県': '中国', '島根県': '中国', '岡山県': '中国', '広島県': '中国', '山口県': '中国',
    '徳島県': '四国', '香川県': '四国', '愛媛県': '四国', '高知県': '四国',
    '福岡県': '九州', '佐賀県': '九州', '長崎県': '九州', '熊本県': '九州',
    '大分県': '九州', '宮崎県': '九州', '鹿児島県': '九州', '沖縄県': '沖縄',
}


def load_checkpoint() -> set[str]:
    """処理済み URL のセットをファイルから読み込む"""
    if not os.path.exists(CHECKPOINT_FILE):
        return set()
    with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip()}


def mark_done(url: str) -> None:
    """処理済み URL をチェックポイントファイルに追記する"""
    with open(CHECKPOINT_FILE, 'a', encoding='utf-8') as f:
        f.write(url + '\n')


def load_ingr_checkpoint() -> set[str]:
    """食材 INSERT 済み URL のセットをファイルから読み込む"""
    if not os.path.exists(INGR_CHECKPOINT_FILE):
        return set()
    with open(INGR_CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip()}


def mark_ingr_done(url: str) -> None:
    """食材 INSERT 済み URL をチェックポイントファイルに追記する"""
    with open(INGR_CHECKPOINT_FILE, 'a', encoding='utf-8') as f:
        f.write(url + '\n')


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return BeautifulSoup(resp.text, 'html.parser')


def get_prefecture_links(soup: BeautifulSoup) -> list[dict]:
    """area/index.html から都道府県ページの URL と都道府県名を取得"""
    result = []
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        # /area/hokkaido.html のようなパターンにマッチ（index.html は除外）
        if '/area/' in href and href.endswith('.html') and 'index' not in href:
            full_url = href if href.startswith('http') else BASE_URL + href
            prefecture_name = a.get_text(strip=True)
            if prefecture_name and full_url not in seen:
                seen.add(full_url)
                result.append({'url': full_url, 'prefecture': prefecture_name})
    return result


def get_recipe_links_from_prefecture(soup: BeautifulSoup, page_url: str, prefecture: str) -> list[dict]:
    """都道府県ページから料理詳細ページの URL を取得"""
    result = []
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/menu/' in href and href.endswith('.html'):
            full_url = urljoin(page_url, href)
            if full_url not in seen:
                seen.add(full_url)
                result.append({'url': full_url, 'prefecture': prefecture})
    return result


def _get_section_text(soup, heading_text: str) -> str | None:
    """指定テキストを含む h3 の直後にある p テキストを返す"""
    for h3 in soup.find_all('h3'):
        if heading_text in h3.get_text():
            sibling = h3.find_next_sibling('p')
            if sibling:
                return sibling.get_text(strip=True)
    return None


def parse_recipe_detail(url: str, prefecture_hint: str | None) -> dict | None:
    """料理詳細ページから情報を取得"""
    try:
        soup = get_soup(url)
    except Exception as e:
        log.warning(f'Failed to fetch {url}: {e}')
        return None

    # メインコンテンツエリアに限定してスクレイピング（ナビ・パンくず・UIを除外）
    main = soup.select_one('#main_content')
    if not main:
        log.warning(f'No #main_content at {url}')
        return None

    # タイトル: <h1> は必ずメインコンテンツ内に1つ（例: "豚丼 北海道"）
    h1 = main.find('h1')
    title_ja = h1.get_text(strip=True) if h1 else None

    if not title_ja:
        log.warning(f'No title found at {url}')
        return None

    # 説明: 「歴史・由来・関連行事」セクションの本文（mainスコープ内で検索）
    description = _get_section_text(main, '歴史・由来・関連行事')

    # 作り方: #main_content 内の <ul class="recipe"> > <li> > <div class="txt">
    instructions_els = main.select('ul.recipe li div.txt')
    instructions_ja = '\n'.join(
        el.get_text(strip=True) for el in instructions_els
    ) if instructions_els else ''

    # 材料: #main_content 内の <ul class="menu_material"> > <li> > <ul class="list"> > <li>[0]=名前, [1]=分量
    ingredients_raw = []
    for item in main.select('ul.menu_material > li'):
        lis = item.select('ul.list > li')
        if len(lis) >= 2:
            name = lis[0].get_text(strip=True)
            amount = lis[1].get_text(strip=True)
            if name:
                ingredients_raw.append({'name': name, 'amount': amount})

    # 都道府県: hint を優先（詳細ページには明示的な都道府県表記がない場合が多い）
    prefecture = prefecture_hint
    region = REGION_MAP.get(prefecture) if prefecture else None

    return {
        'title_ja': title_ja,
        'title_en': '',
        'description': description,
        'description_en': None,
        'instructions_ja': instructions_ja,
        'instructions_en': '',
        'servings': 2,
        'cook_time_min': 30,
        'prefecture': prefecture,
        'region': region,
        'difficulty': None,
        'prep_time_min': None,
        'tags': None,
        'source': 'maff',
        'source_url': url,
        'ingredients_raw': ingredients_raw,
    }


def upsert_recipe(supabase, recipe: dict) -> str | None:
    """recipes テーブルに INSERT し、IDを返す"""
    payload = {k: v for k, v in recipe.items()
               if k not in ('ingredients_raw', 'source_url')}

    # 同一タイトルがあればスキップ
    existing = (
        supabase.table('recipes')
        .select('id')
        .eq('title_ja', payload['title_ja'])
        .eq('source', 'maff')
        .execute()
    )
    if existing.data:
        log.info(f'Skip (already exists): {payload["title_ja"]}')
        return existing.data[0]['id']

    result = supabase.table('recipes').insert(payload).execute()
    if not result.data:
        log.error(f'Insert failed for {payload["title_ja"]}')
        return None
    return result.data[0]['id']


def _execute_with_retry(fn, retries: int = 3, wait: float = 5.0):
    """Supabase クエリを最大 retries 回リトライする（502 などの一時エラー対策）"""
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == retries:
                raise
            log.warning(f'Supabase エラー (試行 {attempt}/{retries}): {e} — {wait}秒後にリトライ')
            time.sleep(wait)


def upsert_ingredients(supabase, recipe_id: str, ingredients_raw: list[dict]):
    """ingredients / recipe_ingredients を upsert"""
    for ing in ingredients_raw:
        name = ing.get('name', '').strip()
        if not name:
            continue

        result = _execute_with_retry(
            lambda n=name: supabase.table('ingredients')
                .upsert({'name_ja': n, 'name_en': ''}, on_conflict='name_ja')
                .execute()
        )
        if not result or not result.data:
            continue
        ingredient_id = result.data[0]['id']

        existing = _execute_with_retry(
            lambda rid=recipe_id, iid=ingredient_id: supabase.table('recipe_ingredients')
                .select('id')
                .eq('recipe_id', rid)
                .eq('ingredient_id', iid)
                .execute()
        )
        if existing and existing.data:
            continue

        _execute_with_retry(
            lambda rid=recipe_id, iid=ingredient_id, amt=ing.get('amount'):
                supabase.table('recipe_ingredients').insert({
                    'recipe_id': rid,
                    'ingredient_id': iid,
                    'amount': amt,
                }).execute()
        )


def reingest_ingredients(supabase) -> None:
    """
    チェックポイントファイルに記録済みのURLを使って、
    既存 maff レシピの食材だけ再スクレイプして INSERT する。
    レシピ自体は再 INSERT しない。
    maff_ingr_done_urls.txt で中断後も続きから再開できる。
    """
    done_urls = load_checkpoint()
    if not done_urls:
        log.error(f'チェックポイントファイル "{CHECKPOINT_FILE}" が見つからないか空です。先に通常スクレイプを実行してください。')
        return

    ingr_done = load_ingr_checkpoint()
    urls = sorted(done_urls)
    skipped = sum(1 for u in urls if u in ingr_done)
    if skipped:
        log.info(f'{skipped} 件は食材INSERT済みのためスキップ')

    pending = [u for u in urls if u not in ingr_done]
    log.info(f'{len(pending)} 件のURLから食材を取得します')

    success = 0
    for i, url in enumerate(pending, 1):
        log.info(f'[{i}/{len(pending)}] {url}')
        time.sleep(SLEEP_SEC)

        try:
            soup = get_soup(url)
        except Exception as e:
            log.warning(f'ページ取得失敗 (スキップ): {url}: {e}')
            continue

        # メインコンテンツに限定
        main = soup.select_one('#main_content')
        if not main:
            log.warning(f'#main_content なし (スキップ): {url}')
            continue

        # タイトルを取得してレシピIDを引く
        h1 = main.find('h1')
        title_ja = h1.get_text(strip=True) if h1 else None

        if not title_ja:
            log.warning(f'タイトル取得失敗 (スキップ): {url}')
            continue

        try:
            existing = _execute_with_retry(
                lambda t=title_ja: supabase.table('recipes')
                    .select('id')
                    .eq('title_ja', t)
                    .eq('source', 'maff')
                    .execute()
            )
        except Exception as e:
            log.warning(f'DB照会失敗 (スキップ): {title_ja}: {e}')
            continue

        if not existing.data:
            log.warning(f'DBにレシピが見つかりません (スキップ): {title_ja}')
            continue

        recipe_id = existing.data[0]['id']

        # 食材を取得
        ingredients_raw = []
        for item in main.select('ul.menu_material > li'):
            lis = item.select('ul.list > li')
            if len(lis) >= 2:
                name = lis[0].get_text(strip=True)
                amount = lis[1].get_text(strip=True)
                if name:
                    ingredients_raw.append({'name': name, 'amount': amount})

        if not ingredients_raw:
            log.warning(f'食材なし (スキップ): {title_ja}')
            continue

        try:
            upsert_ingredients(supabase, recipe_id, ingredients_raw)
        except Exception as e:
            log.warning(f'食材INSERT失敗 (スキップ): {title_ja}: {e}')
            continue

        mark_ingr_done(url)
        ingr_done.add(url)
        log.info(f'  → {len(ingredients_raw)} 件の食材を INSERT: {title_ja}')
        success += 1

    log.info(f'完了: {success}/{len(pending)} 件のレシピに食材を INSERT')


def main():
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_SERVICE_KEY')
    if not supabase_url or not supabase_key:
        raise EnvironmentError('SUPABASE_URL / SUPABASE_SERVICE_KEY が .env に設定されていません')

    supabase = create_client(supabase_url, supabase_key)

    if '--ingredients-only' in sys.argv:
        reingest_ingredients(supabase)
        return

    # Step 1: 都道府県一覧ページから各都道府県ページのリンクを取得
    log.info('都道府県一覧を取得中...')
    area_soup = get_soup(AREA_INDEX_URL)
    pref_links = get_prefecture_links(area_soup)
    log.info(f'{len(pref_links)} 件の都道府県を取得')

    # Step 2: 各都道府県ページから料理リンクを収集
    recipe_links = []
    for pref in pref_links:
        log.info(f'  {pref["prefecture"]}: {pref["url"]}')
        time.sleep(SLEEP_SEC)
        try:
            pref_soup = get_soup(pref['url'])
        except Exception as e:
            log.warning(f'Failed to fetch {pref["url"]}: {e}')
            continue
        links = get_recipe_links_from_prefecture(pref_soup, pref['url'], pref['prefecture'])
        log.info(f'    → {len(links)} 件の料理リンクを取得')
        recipe_links.extend(links)

    log.info(f'合計 {len(recipe_links)} 件の料理リンクを取得')

    # Step 3: 各料理詳細ページをスクレイプして Supabase に保存
    done_urls = load_checkpoint()
    skipped = sum(1 for item in recipe_links if item['url'] in done_urls)
    if skipped:
        log.info(f'{skipped} 件はチェックポイント済みのためスキップ')

    success = 0
    for i, item in enumerate(recipe_links, 1):
        url = item['url']
        if url in done_urls:
            log.debug(f'[{i}/{len(recipe_links)}] スキップ (処理済み): {url}')
            continue

        log.info(f'[{i}/{len(recipe_links)}] {url}')
        time.sleep(SLEEP_SEC)

        recipe = parse_recipe_detail(url, item.get('prefecture'))
        if not recipe:
            continue

        recipe_id = upsert_recipe(supabase, recipe)
        if recipe_id:
            if recipe.get('ingredients_raw'):
                upsert_ingredients(supabase, recipe_id, recipe['ingredients_raw'])
            mark_done(url)
            done_urls.add(url)
            success += 1

    log.info(f'完了: {success}/{len(recipe_links)} 件を INSERT')


if __name__ == '__main__':
    main()
