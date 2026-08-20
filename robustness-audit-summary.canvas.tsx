import { Divider, Grid, H1, H2, Stack, Stat, Table, Text } from 'qoder/canvas';

export default function RobustnessAuditSummary() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>其余模块鲁棒性审计 · 总结报告</H1>
        <Text tone="secondary">
          企业微信客服「护肤顾问」项目(FastAPI)· 只读审计,未改动任何代码 · 2026-08-20
        </Text>
      </Stack>

      <Grid columns={4} gap={16}>
        <Stat value="6" label="确凿 bug(❌)" tone="danger" />
        <Stat value="15" label="概率隐患(⚠️)" tone="warning" />
        <Stat value="11" label="已确认防御点(✅)" />
        <Stat value="3" label="审计区域" />
      </Grid>

      <Divider />

      <H2>审计范围与方法</H2>
      <Table
        headers={['审计区域', '覆盖文件', '推演重点', '方法']}
        rows={[
          [
            '报告联动与欢迎语链路',
            'advisor_report.py、core.py(enter_session/欢迎语)、store.py(transfer/welcome-fallback)',
            'scene_param 畸形、报告 JSON 异常、补发并发去重、转接轮流分配、kf-link 兜底',
            '探索子代理读码审计',
          ],
          [
            'HTTP 入口与协议解析边界',
            'main.py、wecom/handler.py、wecom/crypto.py',
            '畸形 XML/billion laughs(实测 expat 行为)、参数缺失、中间件吞异常、/l/{code} 注入、startup 失败',
            '探索子代理读码审计 + 实测 ElementTree',
          ],
          [
            'core 业务流程与热加载',
            'core.py(orderflow/限流/时间窗)、ai/knowledge.py、links.py',
            'Redis 降级跨模式不一致、乱序回复、ZADD 同秒覆盖、时间窗边界、文件半成品热加载',
            '探索子代理读码审计',
          ],
        ]}
      />
      <Text tone="secondary" size="small">
        三路并行审计后,对关键结论(事件循环阻塞、ZADD member、transfer_complete 无调用方、or 10 兜底)逐一回读源码抽查验证,行号全部吻合。
      </Text>

      <Divider />

      <H2>确凿 bug(按修复优先级)</H2>
      <Table
        headers={['优先级', '问题', '证据位置', '影响']}
        rows={[
          [
            'P0',
            'POST /callback 同步 AI 调用阻塞事件循环(wx-callback 已用 run_in_threadpool,此处漏)',
            'main.py:201-205',
            '每条普通消息最坏阻塞 ~88s,/health 与全部回调冻结,触发企微重推雪崩',
          ],
          [
            'P0',
            '限流 ZADD member 为秒级时间戳,同秒消息互相覆盖(此前审查已提,仍未修)',
            'store.py:196',
            '30条/60s 限流可被同秒轰炸绕过,且与内存降级模式计数语义不一致',
          ],
          [
            'P0',
            'enter_session 处理失败后 msgid 不回滚,进入会话事件永久丢弃',
            'core.py:256-261、440-445',
            '一次瞬时故障即档案同步与欢迎语永久丢失,用户无感知',
          ],
          [
            'P1',
            '非 ASCII x-internal-key 使 hmac.compare_digest 抛 TypeError,被中间件吞成空 200',
            'main.py:112、139',
            '/api/kf-link、/api/pii/* 鉴权失败既无 401 也无错误体',
          ],
          [
            'P1',
            '转人工负载计数只增不减:选中即 INCR、transfer_complete 全仓库无调用方(死代码)、键无 TTL',
            'store.py:260、278',
            '计数永久累积失真,新候选长期吸走全部流量',
          ],
          [
            'P1',
            'SERVICE_START_HOUR=0 被 "or 10" 静默改成 10(END_HOUR 同理)',
            'core.py:465-466',
            '合法配置值 0 无法生效',
          ],
        ]}
        rowTone={['danger', 'danger', 'danger', 'warning', 'warning', 'warning']}
      />

      <Divider />

      <H2>高危隐患 Top 5(⚠️ 共 15 项)</H2>
      <Table
        headers={['隐患', '触发条件', '一句修法']}
        rows={[
          [
            'Redis 降级跨模式三重不一致(orderflow 读空/重推重复处理/旧状态复活)',
            '600s 流程窗口内 Redis 故障或恢复',
            'kv 记录当前模式,模式切换时清空 orderflow 中间态',
          ],
          [
            'AI 失败兜底消息重复发送且限流计数反复累加',
            '兜底路径先 release claim,企微超时重推',
            '兜底发送成功后不回滚 claim',
          ],
          [
            '欢迎语补发:get-then-set 非原子可双发;标记先写后发,发送失败永不重试;welcome_code 失败不回退普通补发',
            '同 scene_param 两条不同 msgid 并发(快速重复点链接)',
            'SET NX EX 原子认领 + 失败回滚标记',
          ],
          [
            'orderflow 乱序回复:任意文本被存为渠道、渠道中 10 位数字误判订单号、orderno 步骤无取消出口',
            '普通用户正常输入即可触发',
            '渠道白名单校验 + 取消词回退 AI',
          ],
          [
            '转接前就删流程状态并提前打 transferred 标记',
            '全员转接失败或非服务时间',
            '转接确认成功后再删状态、再打标记',
          ],
        ]}
        rowTone={['warning', 'warning', 'warning', undefined, undefined]}
      />

      <Divider />

      <H2>已确认处理好的防御点(✅)</H2>
      <Table
        headers={['防御点', '结论']}
        rows={[
          ['畸形 XML 攻击面', 'Python 3.11 expat 内置防护实测拒绝 billion laughs/XXE,畸形 XML 只产生空 200 不崩'],
          ['中间件与 API 端点异常', 'catch_errors 位于 ExceptionMiddleware 外层,422/405/404 不被吞;/api/pii、/api/kf-link 自身 try/except 先于中间件生效'],
          ['查询参数与验签', 'GET 缺参 422、POST 缺参验签失败空 200;签名常量时间比较,大小写变体无绕过路径'],
          ['时间窗边界', '10:00 整包含、20:00 整排除;跨午夜配置启动时拒绝'],
          ['links.json 写坏', 'JSON 损坏保留旧缓存并自动重试'],
          ['知识库为空/全注释', '返回空串,prompt 正常拼装'],
          ['scene_param 畸形/超长', '白名单正则直接拒'],
          ['startup 失败语义', '必填配置快速失败;links 坏保旧缓存;知识库缺空库带警告'],
          ['/l/{code} 注入', '纯 dict 查找,路径穿越无意义,超长 code 只 404'],
          ['Redis INCR 溢出', 'int64 上限业务量不可达'],
          ['单事件异常隔离 / _strip_markdown / prompt 热加载', '均已正确处理,无陈旧 prompt 问题'],
        ]}
      />

      <Divider />

      <H2>修复优先级建议</H2>
      <Stack gap={8}>
        <Text>
          P0(线上可用性/安全):事件循环阻塞 → 限流同秒绕过 → enter_session 失败不 release。每条消息都在触发或安全限流实质失效,应立即修。
        </Text>
        <Text>
          P1(数据正确性):compare_digest 鉴权吞异常、负载计数失真、SERVICE_HOUR=0、Redis 降级跨模式一致性。
        </Text>
        <Text>
          P2(体验与防御纵深):欢迎语并发三件套、orderflow 乱序校验、提前删状态、报告 JSON 类型防护、知识库半成品热加载等,随版本带上。
        </Text>
      </Stack>

      <Text tone="secondary" size="small">
        完整分级明细(全部 6 ❌ / 15 ⚠️ / 11 ✅ 及逐条修法)见对话内审计总报告;本次审计未改动任何代码。
      </Text>
    </Stack>
  );
}
