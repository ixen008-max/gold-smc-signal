name: Forex Signal System

on:
  schedule:
    - cron: '0 * * * 1-5'      # ทุกชั่วโมง จันทร์-ศุกร์
    - cron: '0 0 * * 1-5'      # 7 โมงเช้าไทย
  workflow_dispatch:

jobs:
  gold_smc:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run Gold SMC Signal
        env:
          LINE_CHANNEL_ACCESS_TOKEN: ${{ secrets.LINE_CHANNEL_ACCESS_TOKEN }}
          LINE_USER_ID: ${{ secrets.LINE_USER_ID }}
          TWELVEDATA_API_KEY: ${{ secrets.TWELVEDATA_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python signal_bot.py
      - name: Commit gold state
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add pending_orders.json
          git diff --staged --quiet || (git commit -m "Update gold state [skip ci]" && git push)

  jpy_ichimoku:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run JPY Ichimoku Signal
        env:
          LINE_CHANNEL_ACCESS_TOKEN: ${{ secrets.LINE_CHANNEL_ACCESS_TOKEN }}
          LINE_USER_ID: ${{ secrets.LINE_USER_ID }}
          TWELVEDATA_API_KEY: ${{ secrets.TWELVEDATA_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python ichimoku_signal.py
      - name: Commit ichimoku state
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add ichimoku_pending.json
          git diff --staged --quiet || (git commit -m "Update ichimoku state [skip ci]" && git push)