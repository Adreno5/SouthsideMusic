from __future__ import annotations

import base64
import ctypes
import hashlib
import html
import json
import logging
import re
import secrets
import threading
import time
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, NamedTuple
from uuid import uuid4

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from core.lyric_formats import (
    LyricLine,
    alignTranslation,
    contentLines,
    hasWordTiming,
    krcDecrypt,
    krcTranslations,
    parseAny,
    parseKrc,
    parseLrc,
    parseRichsync,
    qrcDecrypt,
    toLrc,
    toYrc,
    translationTexts,
    yrcToLrc,
)

_logger = logging.getLogger(__name__)

_TIMEOUT = 6
_UA = (
    'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36'
)
_WEB_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
_QQ_HEADERS = {'User-Agent': _UA, 'Referer': 'https://c.y.qq.com/'}
_KUGOU_HEADERS = {'User-Agent': _UA}
_SODA_HEADERS = {
    'Accept': '*/*',
    'User-Agent': (
        'com.luna.music/100198030 (Linux; U; Android 15; zh_CN_#Hans; '
        'ABR-AL80; Build/V417IR;tt-ok/3.12.13.19)'
    ),
}
_LRCLIB_HEADERS = {'User-Agent': 'SouthsideMusic (https://github.com/)'}
_MUSIXMATCH_HEADERS = {
    'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 13)',
    'Cookie': 'AWSELB=0; AWSELBCORS=0',
}
_MUSIXMATCH_BASE = 'https://apic.musixmatch.com/ws/1.1/'
_MUSIXMATCH_APP_ID = 'android-player-v1.0'

_SODA_QUERY = {
    'device_platform': 'android',
    'os': 'android',
    'ssmix': 'a',
    'cdid': '46556f98-1720-4248-83da-62b74b60b46a',
    'channel': 'xiaomi_8478_64',
    'aid': '386088',
    'app_name': 'luna',
    'version_code': '100198030',
    'version_name': '19.8.0',
    'manifest_version_code': '100198030',
    'update_version_code': '100198030',
    'resolution': '1080*1920',
    'dpi': '480',
    'device_type': 'ABR-AL80',
    'device_brand': 'HUAWEI',
    'language': 'zh',
    'os_api': '35',
    'os_version': '15',
    'ac': 'wifi',
    'device_model': 'ABR-AL80',
    'tz_name': 'Asia/Shanghai',
    'tz_offset': '28800',
    'package': 'com.luna.music',
    'sim_region': 'cn',
    'iid': str(secrets.randbelow(99999999 - 10000000) + 10000000)
    + str(secrets.randbelow(99999999 - 10000000) + 10000000),
    'device_id': str(secrets.randbelow(99999999 - 10000000) + 10000000)
    + str(secrets.randbelow(99999999 - 10000000) + 10000000),
    'cursor': '0',
    'count': '20',
}

_CHINESE_MAP = str.maketrans(
    (
        '皚藹礙愛骯翺襖奧壩罷擺敗頒辦絆幫綁鎊謗剝飽寶報鮑輩貝鋇狽備憊繃筆畢斃幣閉邊編貶變辯辮標鱉別癟瀕濱賓擯餅並撥缽鉑駁蔔補財參蠶殘慚慘燦'
        '蒼艙倉滄廁側冊測層詫攙摻蟬饞讒纏鏟產闡顫場嘗嚐長償腸廠暢鈔車徹塵沈陳襯撐稱懲誠騁癡遲馳恥齒熾沖蟲寵疇躊籌綢醜櫥廚鋤雛礎儲觸處傳瘡闖'
        '創錘純綽辭詞賜聰蔥囪從叢湊躥竄錯達帶貸擔單鄲撣膽憚誕彈當擋黨蕩檔搗島禱導盜燈鄧敵滌遞締顛點墊電澱釣調諜疊釘頂錠訂丟東動棟凍鬥犢獨讀'
        '賭鍍鍛斷緞兌隊對噸頓鈍奪墮鵝額訛惡餓兒爾餌貳發罰閥琺礬釩煩範販飯訪紡飛誹廢費紛墳奮憤糞豐楓鋒風瘋馮縫諷鳳膚輻撫輔賦復負訃婦縛該鈣蓋'
        '乾幹桿趕稈贛岡剛鋼綱崗臯鎬擱鴿閣鉻個給龔宮鞏貢鉤溝茍構購夠蠱顧剮掛關觀館慣貫廣規矽歸龜閨軌詭櫃貴劊輥滾鍋國過駭韓漢號閡鶴賀橫轟鴻紅'
        '後壺護滬戶嘩華畫劃話懷壞歡環還緩換喚瘓煥渙黃謊揮輝毀賄穢會燴匯諱誨繪葷渾夥獲貨禍擊機積饑跡蹟譏雞績緝極輯級擠幾薊劑濟計記際繼紀夾莢'
        '頰賈鉀價駕殲監堅箋間艱緘繭檢堿鹼揀撿簡儉減薦檻鑒踐賤見鍵艦劍餞漸濺澗將漿蔣槳獎講醬膠澆驕嬌攪鉸矯僥腳餃繳絞轎較稭階節莖鯨驚經頸靜鏡'
        '徑痙競凈糾廄舊駒舉據鋸懼劇鵑絹傑潔結誡屆緊錦僅謹進晉燼盡勁荊覺決訣絕鈞軍駿開凱顆殼課墾懇摳庫褲誇塊儈寬礦曠況虧巋窺饋潰擴闊蠟臘萊來'
        '賴藍欄攔籃闌蘭瀾讕攬覽懶纜爛濫瑯撈勞澇樂鐳壘類淚籬貍離裡裏鯉禮麗厲勵礫歷瀝隸倆聯蓮連鐮憐漣簾斂臉鏈戀煉練糧涼兩輛諒療遼鐐獵臨鄰鱗凜'
        '賃齡鈴淩靈嶺領餾劉龍聾嚨籠壟攏隴樓婁摟簍蘆盧顱廬爐擄鹵虜魯賂祿錄陸驢呂鋁侶屢縷慮濾綠巒攣孿灤亂掄輪倫侖淪綸論蘿羅囉邏鑼籮騾駱絡媽瑪'
        '碼螞馬罵嗎買麥賣邁脈瞞饅蠻滿謾貓錨鉚貿麼麽黴沒鎂門悶們錳夢瞇謎彌覓冪綿緬廟滅憫閩鳴銘謬謀畝吶鈉納難撓腦惱鬧餒內擬妳膩攆撚釀鳥聶嚙鑷'
        '鎳檸獰寧擰濘鈕紐膿濃農瘧諾歐鷗毆嘔漚盤龐拋賠噴鵬騙飄頻貧蘋憑評潑頗撲鋪樸譜棲淒臍齊騎豈啟氣棄訖牽扡釬鉛遷簽謙錢鉗潛淺譴塹槍嗆牆墻薔'
        '強搶鍬橋喬僑翹竅竊欽親寢輕氫傾頃請慶瓊窮趨區軀驅齲顴權勸卻鵲確讓饒擾繞熱韌認紉榮絨軟銳閏潤灑薩鰓賽傘喪騷掃澀殺剎紗篩曬刪閃陜贍繕墑'
        '傷賞燒紹賒攝懾設紳審嬸腎滲聲繩勝聖師獅濕詩屍時蝕實識駛勢適釋飾視試壽獸樞輸書贖屬術樹豎數帥雙誰稅順說説碩爍絲飼聳慫頌訟誦擻蘇訴肅雖'
        '隨綏歲孫損筍縮瑣鎖獺撻擡臺態攤貪癱灘壇譚談嘆湯燙濤絳討騰謄銻題體屜條貼鐵廳聽烴銅統頭禿圖塗團頹蛻脫鴕馱駝橢窪襪彎灣頑萬網韋違圍為爲'
        '濰維葦偉偽僞緯餵謂衛溫聞紋穩問甕撾蝸渦窩臥嗚鎢烏汙誣無蕪吳塢霧務誤錫犧襲習銑戲細蝦轄峽俠狹廈嚇鍁鮮纖鹹賢銜閑顯險現獻縣餡羨憲線廂鑲'
        '鄉詳響項蕭囂銷曉嘯蠍協挾攜脅諧寫瀉謝鋅釁興兇洶銹繡虛噓須許敘緒續軒懸選癬絢學勛詢尋馴訓訊遜壓鴉鴨啞亞訝閹煙菸鹽嚴巖顏閻艷厭硯彥諺驗'
        '鴦楊揚瘍陽癢養樣瑤搖堯遙窯謠藥爺頁業葉醫銥頤遺儀彜蟻藝億憶義詣議誼譯異繹蔭陰銀飲隱櫻嬰鷹應纓瑩螢營熒蠅贏穎喲擁傭癰踴詠湧優憂郵鈾猶'
        '遊誘於輿魚漁娛與嶼語籲禦癒獄譽預馭鴛淵轅園員圓緣遠願約躍鑰嶽粵悅閱雲鄖勻隕運蘊醞暈韻雜災載攢暫贊贓臟鑿棗竈責擇則澤賊贈紮劄軋鍘閘柵'
        '詐齋債氈盞斬輾嶄棧戰綻張漲帳賬脹趙蟄轍鍺這貞針偵診鎮陣掙睜猙爭幀癥鄭證織職執紙誌摯擲幟製質滯鐘終種腫眾謅軸皺晝驟豬諸誅燭矚囑貯鑄築'
        '註駐專磚轉賺樁莊裝妝壯狀錐贅墜綴諄準濁茲資漬蹤綜總縱鄒詛組鑽鉆錒噯嬡璦曖靄諳銨鵪媼驁鰲鈀唄鈑鴇齙鵯賁錛蓽嗶潷鉍篳蹕芐緶籩驃颮飆鏢鑣'
        '鰾儐繽檳殯臏鑌髕鬢稟餑鈸鵓鈽驂黲惻鍤儕釵囅諂讖蕆懺嬋驏覘禪鐔倀萇悵閶鯧硨傖諶櫬磣齔棖檉鋮鐺飭鴟銃儔幬讎芻絀躕釧愴綞鶉輟齪鶿蓯驄樅輳'
        '攛銼鹺噠韃駘紿殫賧癉簞讜碭襠燾鐙糴詆諦綈覿鏑巔鈿癲銚鯛鰈鋌銩崠鶇竇瀆櫝牘篤黷籪懟鐓燉躉鐸諤堊閼軛鋨鍔鶚顎顓鱷誒邇鉺鴯鮞鈁魴緋鐨鯡僨'
        '灃鳧駙紱紼賻麩鮒鰒釓賅尷搟紺戇睪誥縞鋯紇鎘潁亙賡綆鯁詬緱覯詁轂鈷錮鴣鵠鶻鴰摑詿摜鸛鰥獷匭劌媯檜鮭鱖袞緄鯀堝咼幗槨蟈鉿闞絎頡灝顥訶闔'
        '蠣黌訌葒閎鱟滸鶘驊樺鏵奐繯鍰鯇鰉詼薈噦澮繢琿暉諢餛閽鈥鑊訐詰薺嘰嚌驥璣覬齏磯羈蠆躋霽鱭鯽郟浹鋏鎵蟯諫縑戔戩瞼鶼筧鰹韉韁撟嶠鷦鮫癤頜'
        '鮚巹藎饉縉贐覲剄涇逕弳脛靚鬮鳩鷲詎屨櫸颶鉅鋦窶齟錈鐫雋譎玨皸剴塏愾愷鎧鍇龕閌鈧銬騍緙軻鈳錁頷齦鏗嚳鄶噲膾獪髖誆誑鄺壙纊貺匱蕢憒聵簣'
        '閫錕鯤蠐崍徠淶瀨賚睞錸癩籟嵐欖斕鑭襤閬鋃嘮嶗銠鐒癆鰳誄縲儷酈壢藶蒞蘺嚦邐驪縭櫪櫟轢礪鋰鸝癘糲躒靂鱺鱧蘞奩瀲璉殮褳襝鰱魎繚釕鷯藺廩檁'
        '轔躪綾欞蟶鯪瀏騮綹鎦鷚蘢瀧瓏櫳朧礱僂蔞嘍嶁鏤瘺耬螻髏壚擼嚕閭瀘淥櫨櫓轤輅轆氌臚鸕鷺艫鱸臠孌欒鸞鑾圇犖玀濼欏腡鏍櫚褸鋝嘸嘜嬤榪勱縵鏝'
        '顙鰻捫燜懣鍆羋謐獼禰澠靦黽緲繆閔緡謨驀饃歿鏌鉬鐃訥鈮鯢輦鯰蔦裊隉蘗囁顢躡苧嚀聹儂噥駑釹儺謳慪甌蹣皰轡紕羆鈹諞駢縹嬪釙鏷鐠蘄騏綺榿磧'
        '頎頏鰭僉蕁慳騫繾槧鈐嬙檣戧熗錆鏘鏹羥蹌誚譙蕎繰磽蹺愜鍥篋鋟撳鯖煢蛺巰賕蟣鰍詘嶇闃覷鴝詮綣輇銓闋闕愨蕘嬈橈飪軔嶸蠑縟銣顰蜆颯毿糝繅嗇'
        '銫穡鎩鯊釃訕姍騸釤鱔坰殤觴厙灄畬詵諗瀋謚塒蒔弒軾貰鈰鰣綬攄紓閂鑠廝駟緦鍶鷥藪餿颼鎪謖穌誶蓀猻嗩脧闥鉈鰨鈦鮐曇鉭錟頇儻餳鐋鏜韜鋱緹鵜'
        '闐糶齠鰷慟鈄釷摶飩籜鼉媧膃紈綰輞諉幃闈溈潿瑋韙煒鮪閿萵齷鄔廡憮嫵騖鵡鶩餼鬩璽覡硤莧薟蘚峴獫嫻鷴癇蠔秈躚薌餉驤緗饗嘵瀟驍綃梟簫褻擷紲'
        '纈陘滎饈鵂詡頊諼鉉鏇謔澩鱈塤潯鱘埡婭椏氬厴贗儼兗讞懨閆釅魘饜鼴煬軺鷂鰩靨謁鄴曄燁詒囈嶧飴懌驛縊軼貽釔鎰鐿瘞艤銦癮塋鶯縈鎣攖嚶瀅瀠瓔'
        '鸚癭頦罌鏞蕕銪魷傴俁諛諭蕷崳飫閾嫗紆覦歟鈺鵒鷸齬櫞鳶黿鉞鄆蕓惲慍紜韞殞氳瓚趲鏨駔賾嘖幘簀譖繒譫詔釗謫輒鷓湞縝楨軫賑禎鴆諍崢鉦錚箏騭'
        '櫛梔軹輊贄鷙螄縶躓躑觶鍾紂縐佇櫧銖囀饌顳騅縋諑鐲諮緇輜貲眥錙齜鯔傯諏騶鯫鏃纘躦鱒訁譾郤氹阪堖垵檾蕒葤蓧蒓槁摣咤唚哢噝噅襆嶴獁麅餘餷'
        '饊饢怵懍爿漵灩瀦糸絝緔瑉梘棬橰櫫軲軤賫膁腖飈煆湣碸瞘鈈鉕鋣銱鋥鋶鐦鐧鍩鍀鍃錇鎄鎇鎿鐝鑥鑹鑔穭鶓鶥鸌癧屙瘂臒襇繈耮顬蟎麯鮁鮃鮎鯗鯝鯴'
        '鱝鯿鰠鰵鱅鞽韝齇'
    ),
    (
        '皑蔼碍爱肮翱袄奥坝罢摆败颁办绊帮绑镑谤剥饱宝报鲍辈贝钡狈备惫绷笔毕毙币闭边编贬变辩辫标鳖别瘪濒滨宾摈饼并拨钵铂驳卜补财参蚕残惭惨灿'
        '苍舱仓沧厕侧册测层诧搀掺蝉馋谗缠铲产阐颤场尝尝长偿肠厂畅钞车彻尘沉陈衬撑称惩诚骋痴迟驰耻齿炽冲虫宠畴踌筹绸丑橱厨锄雏础储触处传疮闯'
        '创锤纯绰辞词赐聪葱囱从丛凑蹿窜错达带贷担单郸掸胆惮诞弹当挡党荡档捣岛祷导盗灯邓敌涤递缔颠点垫电淀钓调谍叠钉顶锭订丢东动栋冻斗犊独读'
        '赌镀锻断缎兑队对吨顿钝夺堕鹅额讹恶饿儿尔饵贰发罚阀珐矾钒烦范贩饭访纺飞诽废费纷坟奋愤粪丰枫锋风疯冯缝讽凤肤辐抚辅赋复负讣妇缚该钙盖'
        '干干杆赶秆赣冈刚钢纲岗皋镐搁鸽阁铬个给龚宫巩贡钩沟苟构购够蛊顾剐挂关观馆惯贯广规硅归龟闺轨诡柜贵刽辊滚锅国过骇韩汉号阂鹤贺横轰鸿红'
        '后壶护沪户哗华画划话怀坏欢环还缓换唤痪焕涣黄谎挥辉毁贿秽会烩汇讳诲绘荤浑伙获货祸击机积饥迹迹讥鸡绩缉极辑级挤几蓟剂济计记际继纪夹荚'
        '颊贾钾价驾歼监坚笺间艰缄茧检碱硷拣捡简俭减荐槛鉴践贱见键舰剑饯渐溅涧将浆蒋桨奖讲酱胶浇骄娇搅铰矫侥脚饺缴绞轿较秸阶节茎鲸惊经颈静镜'
        '径痉竞净纠厩旧驹举据锯惧剧鹃绢杰洁结诫届紧锦仅谨进晋烬尽劲荆觉决诀绝钧军骏开凯颗壳课垦恳抠库裤夸块侩宽矿旷况亏岿窥馈溃扩阔蜡腊莱来'
        '赖蓝栏拦篮阑兰澜谰揽览懒缆烂滥琅捞劳涝乐镭垒类泪篱狸离里里鲤礼丽厉励砾历沥隶俩联莲连镰怜涟帘敛脸链恋炼练粮凉两辆谅疗辽镣猎临邻鳞凛'
        '赁龄铃凌灵岭领馏刘龙聋咙笼垄拢陇楼娄搂篓芦卢颅庐炉掳卤虏鲁赂禄录陆驴吕铝侣屡缕虑滤绿峦挛孪滦乱抡轮伦仑沦纶论萝罗啰逻锣箩骡骆络妈玛'
        '码蚂马骂吗买麦卖迈脉瞒馒蛮满谩猫锚铆贸么么霉没镁门闷们锰梦眯谜弥觅幂绵缅庙灭悯闽鸣铭谬谋亩呐钠纳难挠脑恼闹馁内拟你腻撵捻酿鸟聂啮镊'
        '镍柠狞宁拧泞钮纽脓浓农疟诺欧鸥殴呕沤盘庞抛赔喷鹏骗飘频贫苹凭评泼颇扑铺朴谱栖凄脐齐骑岂启气弃讫牵扦钎铅迁签谦钱钳潜浅谴堑枪呛墙墙蔷'
        '强抢锹桥乔侨翘窍窃钦亲寝轻氢倾顷请庆琼穷趋区躯驱龋颧权劝却鹊确让饶扰绕热韧认纫荣绒软锐闰润洒萨鳃赛伞丧骚扫涩杀刹纱筛晒删闪陕赡缮墒'
        '伤赏烧绍赊摄慑设绅审婶肾渗声绳胜圣师狮湿诗尸时蚀实识驶势适释饰视试寿兽枢输书赎属术树竖数帅双谁税顺说说硕烁丝饲耸怂颂讼诵擞苏诉肃虽'
        '随绥岁孙损笋缩琐锁獭挞抬台态摊贪瘫滩坛谭谈叹汤烫涛绦讨腾誊锑题体屉条贴铁厅听烃铜统头秃图涂团颓蜕脱鸵驮驼椭洼袜弯湾顽万网韦违围为为'
        '潍维苇伟伪伪纬喂谓卫温闻纹稳问瓮挝蜗涡窝卧呜钨乌污诬无芜吴坞雾务误锡牺袭习铣戏细虾辖峡侠狭厦吓锨鲜纤咸贤衔闲显险现献县馅羡宪线厢镶'
        '乡详响项萧嚣销晓啸蝎协挟携胁谐写泻谢锌衅兴凶汹锈绣虚嘘须许叙绪续轩悬选癣绚学勋询寻驯训讯逊压鸦鸭哑亚讶阉烟烟盐严岩颜阎艳厌砚彦谚验'
        '鸯杨扬疡阳痒养样瑶摇尧遥窑谣药爷页业叶医铱颐遗仪彝蚁艺亿忆义诣议谊译异绎荫阴银饮隐樱婴鹰应缨莹萤营荧蝇赢颖哟拥佣痈踊咏涌优忧邮铀犹'
        '游诱于舆鱼渔娱与屿语吁御愈狱誉预驭鸳渊辕园员圆缘远愿约跃钥岳粤悦阅云郧匀陨运蕴酝晕韵杂灾载攒暂赞赃脏凿枣灶责择则泽贼赠扎札轧铡闸栅'
        '诈斋债毡盏斩辗崭栈战绽张涨帐账胀赵蛰辙锗这贞针侦诊镇阵挣睁狰争帧症郑证织职执纸志挚掷帜制质滞钟终种肿众诌轴皱昼骤猪诸诛烛瞩嘱贮铸筑'
        '注驻专砖转赚桩庄装妆壮状锥赘坠缀谆准浊兹资渍踪综总纵邹诅组钻钻锕嗳嫒瑷暧霭谙铵鹌媪骜鳌钯呗钣鸨龅鹎贲锛荜哔滗铋筚跸苄缏笾骠飑飙镖镳'
        '鳔傧缤槟殡膑镔髌鬓禀饽钹鹁钸骖黪恻锸侪钗冁谄谶蒇忏婵骣觇禅镡伥苌怅阊鲳砗伧谌榇碜龀枨柽铖铛饬鸱铳俦帱雠刍绌蹰钏怆缍鹑辍龊鹚苁骢枞辏'
        '撺锉鹾哒鞑骀绐殚赕瘅箪谠砀裆焘镫籴诋谛绨觌镝巅钿癫铫鲷鲽铤铥岽鸫窦渎椟牍笃黩簖怼镦炖趸铎谔垩阏轭锇锷鹗颚颛鳄诶迩铒鸸鲕钫鲂绯镄鲱偾'
        '沣凫驸绂绋赙麸鲋鳆钆赅尴擀绀戆睾诰缟锆纥镉颍亘赓绠鲠诟缑觏诂毂钴锢鸪鹄鹘鸹掴诖掼鹳鳏犷匦刿妫桧鲑鳜衮绲鲧埚呙帼椁蝈铪阚绗颉灏颢诃阖'
        '蛎黉讧荭闳鲎浒鹕骅桦铧奂缳锾鲩鳇诙荟哕浍缋珲晖诨馄阍钬镬讦诘荠叽哜骥玑觊齑矶羁虿跻霁鲚鲫郏浃铗镓蛲谏缣戋戬睑鹣笕鲣鞯缰挢峤鹪鲛疖颌'
        '鲒卺荩馑缙赆觐刭泾迳弪胫靓阄鸠鹫讵屦榉飓钜锔窭龃锩镌隽谲珏皲剀垲忾恺铠锴龛闶钪铐骒缂轲钶锞颔龈铿喾郐哙脍狯髋诓诳邝圹纩贶匮蒉愦聩篑'
        '阃锟鲲蛴崃徕涞濑赉睐铼癞籁岚榄斓镧褴阆锒唠崂铑铹痨鳓诔缧俪郦坜苈莅蓠呖逦骊缡枥栎轹砺锂鹂疠粝跞雳鲡鳢蔹奁潋琏殓裢裣鲢魉缭钌鹩蔺廪檩'
        '辚躏绫棂蛏鲮浏骝绺镏鹨茏泷珑栊胧砻偻蒌喽嵝镂瘘耧蝼髅垆撸噜闾泸渌栌橹轳辂辘氇胪鸬鹭舻鲈脔娈栾鸾銮囵荦猡泺椤脶镙榈褛锊呒唛嬷杩劢缦镘'
        '颡鳗扪焖懑钔芈谧猕祢渑腼黾缈缪闵缗谟蓦馍殁镆钼铙讷铌鲵辇鲶茑袅陧蘖嗫颟蹑苎咛聍侬哝驽钕傩讴怄瓯蹒疱辔纰罴铍谝骈缥嫔钋镤镨蕲骐绮桤碛'
        '颀颃鳍佥荨悭骞缱椠钤嫱樯戗炝锖锵镪羟跄诮谯荞缲硗跷惬锲箧锓揿鲭茕蛱巯赇虮鳅诎岖阒觑鸲诠绻辁铨阕阙悫荛娆桡饪轫嵘蝾缛铷颦蚬飒毵糁缫啬'
        '铯穑铩鲨酾讪姗骟钐鳝垧殇觞厍滠畲诜谂渖谥埘莳弑轼贳铈鲥绶摅纾闩铄厮驷缌锶鸶薮馊飕锼谡稣谇荪狲唢睃闼铊鳎钛鲐昙钽锬顸傥饧铴镗韬铽缇鹈'
        '阗粜龆鲦恸钭钍抟饨箨鼍娲腽纨绾辋诿帏闱沩涠玮韪炜鲔阌莴龌邬庑怃妩骛鹉鹜饩阋玺觋硖苋莶藓岘猃娴鹇痫蚝籼跹芗饷骧缃飨哓潇骁绡枭箫亵撷绁'
        '缬陉荥馐鸺诩顼谖铉镟谑泶鳕埙浔鲟垭娅桠氩厣赝俨兖谳恹闫酽魇餍鼹炀轺鹞鳐靥谒邺晔烨诒呓峄饴怿驿缢轶贻钇镒镱瘗舣铟瘾茔莺萦蓥撄嘤滢潆璎'
        '鹦瘿颏罂镛莸铕鱿伛俣谀谕蓣嵛饫阈妪纡觎欤钰鹆鹬龉橼鸢鼋钺郓芸恽愠纭韫殒氲瓒趱錾驵赜啧帻箦谮缯谵诏钊谪辄鹧浈缜桢轸赈祯鸩诤峥钲铮筝骘'
        '栉栀轵轾贽鸷蛳絷踬踯觯锺纣绉伫槠铢啭馔颞骓缒诼镯谘缁辎赀眦锱龇鲻偬诹驺鲰镞缵躜鳟讠谫郄凼坂垴埯苘荬荮莜莼藁揸吒吣咔咝咴幞岙犸狍馀馇'
        '馓馕憷懔丬溆滟潴纟绔绱珉枧桊槔橥轱轷赍肷胨飚煅愍砜眍钚钷铘铞锃锍锎锏锘锝锪锫锿镅镎镢镥镩镲稆鹋鹛鹱疬疴痖癯裥襁耢颥螨麴鲅鲆鲇鲞鲴鲺'
        '鲼鳊鳋鳘鳙鞒鞴齄'
    ),
)

_MUSIXMATCH_LOCK = threading.Lock()
_MUSIXMATCH_TOKEN_LOCK = threading.Lock()
_musixmatch_token = ''
_musixmatch_last_request = 0.0


@dataclass(frozen=True)
class _Track:
    title: str
    artists: tuple[str, ...]
    duration: int

    @property
    def artist(self) -> str:
        return ', '.join(self.artists)


@dataclass
class LyricCandidate:
    source: str
    lyric: str
    yrc_lyric: str
    translated_lyric: str
    has_word: bool
    translation_source: str = ''


class _Raw(NamedTuple):
    source: str
    lyric: str
    yrc_lyric: str
    has_word: bool
    translations: list[str]


class _Pick(NamedTuple):
    name: str
    duration: int
    payload: Any
    artists: tuple[str, ...] = ()


def _toInt(value: object) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _simplified(text: str) -> str:
    if not text:
        return ''
    convert = ctypes.windll.kernel32.LCMapStringEx
    length = len(text.encode('utf-16-le', 'surrogatepass')) // 2
    size = convert('zh-CN', 0x02000000, text, length, None, 0, None, None, 0)
    if size:
        buffer = ctypes.create_unicode_buffer(size)
        if convert('zh-CN', 0x02000000, text, length, buffer, size, None, None, 0):
            text = buffer.value
    return (
        text
        .translate(_CHINESE_MAP)
        .replace('藉', '借')
        .replace('咀', '嘴')
        .replace('昇', '升')
        .replace('髒', '脏')
    )


def _normalize(text: str) -> str:
    text = _simplified(text).lower().strip()
    for old, new in (
        ('’', "'"),
        ('，', ','),
        ('（', '('),
        ('）', ')'),
        ('[', '('),
        (']', ')'),
    ):
        text = text.replace(old, new)
    while '  ' in text:
        text = text.replace('  ', ' ')
    return text.replace(' (', '(').replace('( ', '(').replace(' )', ')')


def _nameScore(first: str, second: str) -> int:
    if not first.strip() or not second.strip():
        return 0
    first, second = _normalize(first), _normalize(second)
    if first == second:
        return 7
    first = first.replace('acoustic version', 'acoustic')
    second = second.replace('acoustic version', 'acoustic')
    if (first.replace(' - ', ' (').strip() + ')').replace(' ', '') == (
        second.replace(' - ', ' (').strip() + ')'
    ).replace(' ', ''):
        return 6
    for special in (
        'deluxe',
        'explicit',
        'special edition',
        'bonus track',
        'feat',
        'with',
    ):
        marker = '(' + special
        if (
            marker in first
            and marker not in second
            and first.split(marker)[0].strip() == second
        ):
            return 6
        if (
            marker in second
            and marker not in first
            and second.split(marker)[0].strip() == first
        ):
            return 6
    for left, right in (
        ('feat', 'explicit'),
        ('with', 'explicit'),
        ('feat', 'feat'),
        ('with', 'with'),
    ):
        for a, b in ((first, second), (second, first)):
            if (
                '(' + left in a
                and '(' + right in b
                and a.split('(' + left)[0].strip() == b.split('(' + right)[0].strip()
            ):
                return 5
    if '(' in first and '(' not in second and first.split('(')[0].strip() == second:
        return 4
    if '(' in second and '(' not in first and second.split('(')[0].strip() == first:
        return 4
    first_units = memoryview(first.encode('utf-16-le', 'surrogatepass')).cast('H')
    second_units = memoryview(second.encode('utf-16-le', 'surrogatepass')).cast('H')
    if len(first_units) == len(second_units):
        same = sum(a == b for a, b in zip(first_units, second_units)) / len(first_units)
        if (same >= 0.8 and len(first_units) >= 4) or (
            same >= 0.5 and 2 <= len(first_units) <= 3
        ):
            return 5
    previous = [0] * (len(second_units) + 1)
    for unit_a in first_units:
        current = [0]
        for index, unit_b in enumerate(second_units):
            current.append(
                previous[index] + 1
                if unit_a == unit_b
                else max(previous[index + 1], current[-1])
            )
        previous = current
    similarity = round(previous[-1] / max(len(first_units), len(second_units)) * 100, 2)
    for threshold, score in ((90, 6), (80, 5), (68, 4), (55, 2)):
        if similarity > threshold:
            return score
    return 0


def _artistScore(first: Sequence[str], second: Sequence[str]) -> int:
    a = [_simplified(name.lower()) for name in first if name.strip()]
    b = [_simplified(name.lower()) for name in second if name.strip()]
    if not a or not b:
        return 0
    count = sum(name in a for name in b)
    if count == len(a) == len(b):
        return 7
    if (count + 1 >= len(a) and len(a) >= 2) or (len(a) > 6 and count / len(a) > 0.8):
        return 6
    if count == 1 and len(a) == 1 and len(b) == 2:
        return 5
    if len(a) > 5 and ('Various' in b[0] or '群星' in b[0]):
        return 6
    if len(a) > 7 and len(b) > 7 and count / len(a) > 0.66:
        return 5
    if len(a) == 1 and len(b) > 1:
        if a[0].startswith(b[0]) or (len(b[0]) > 3 and b[0] in a[0]):
            return 5
        if (len(b[0]) > 1 and b[0] in a[0]) or count == 1:
            return 4
    return 2 if count >= 2 else 0


def _matchType(track: _Track, item: _Pick) -> int:
    total = _nameScore(track.title, item.name) + _artistScore(
        track.artists, item.artists
    )
    available = 14
    if track.duration and item.duration:
        difference = abs(track.duration - item.duration)
        total += next(
            score
            for threshold, score in (
                (1, 7),
                (300, 6),
                (700, 5),
                (1500, 4),
                (3500, 2),
                (float('inf'), 0),
            )
            if difference < threshold
        )
        available += 7
    score = total * 25.2 / available
    return next(
        match
        for threshold, match in (
            (21, 100),
            (19, 99),
            (17, 95),
            (15, 90),
            (11, 70),
            (8, 30),
            (3, 10),
            (-1, -1),
        )
        if score > threshold
    )


def _searchTrack(
    search: Callable[[str], list[_Pick]], track: _Track, cancel: threading.Event
) -> _Pick | None:
    query = f'{track.title} {track.artist.replace(", ", " ")}'.replace(
        ' - ', ' '
    ).strip()
    title = track.title.split('(feat.')[0].split(' - feat.')[0].strip()
    queries = [query]
    shorter = f'{title} {track.artist.replace(", ", " ")}'.replace(' - ', ' ').strip()
    if shorter != query:
        queries.extend((shorter, title.replace(' - ', ' ').strip()))
    results: list[_Pick] = []
    for full in (False, True):
        results.clear()
        for keyword in queries:
            if cancel.is_set():
                return None
            try:
                found = search(keyword)
            except (requests.RequestException, ValueError, TypeError, KeyError):
                found = []
            results.extend(found)
            if found and not full:
                break
        results.sort(key=lambda item: _matchType(track, item), reverse=True)
        if results and _matchType(track, results[0]) >= 70:
            return results[0]
    return None


def _textRaw(
    source: str, lines: Sequence[LyricLine], translations: Sequence[str]
) -> _Raw | None:
    content = contentLines(lines)
    if not content:
        return None
    word = hasWordTiming(content)
    return _Raw(
        source=source,
        lyric=toLrc(content),
        yrc_lyric=toYrc(content) if word else '',
        has_word=word,
        translations=list(translations),
    )


def _buildRaw(lyrics: Mapping[str, str]) -> _Raw | None:
    lyric = (lyrics.get('lyric') or '').strip()
    yrc = (lyrics.get('yrc_lyric') or '').strip()
    if not lyric and not yrc:
        return None
    return _Raw(
        source='cache',
        lyric=lyric or yrcToLrc(yrc),
        yrc_lyric=yrc,
        has_word=bool(yrc),
        translations=translationTexts(lyrics.get('translated_lyric') or ''),
    )


def _neteaseRaw(source: str, lyric: str, yrc: str, translated: str) -> _Raw | None:
    lyric = lyric.strip()
    yrc = yrc.strip()
    if not lyric and not yrc:
        return None
    return _Raw(
        source=source,
        lyric=lyric or yrcToLrc(yrc),
        yrc_lyric=yrc,
        has_word=bool(yrc),
        translations=translationTexts(translated),
    )


def _assemble(raw: _Raw, translation: _Raw | None) -> LyricCandidate:
    display = raw.yrc_lyric if raw.has_word and raw.yrc_lyric else raw.lyric
    translated = ''
    translation_source = ''
    if translation is not None:
        translated = alignTranslation(parseAny(display), translation.translations)
        translation_source = translation.source
    return LyricCandidate(
        source=raw.source,
        lyric=raw.lyric,
        yrc_lyric=raw.yrc_lyric,
        translated_lyric=translated,
        has_word=raw.has_word,
        translation_source=translation_source,
    )


def _fetchNeteaseNcm(netease_id: str, cancel: threading.Event) -> _Raw | None:
    if not netease_id or cancel.is_set():
        return None
    from core.backend import getBackend

    info = getBackend().getTrackLyrics(netease_id)
    return _neteaseRaw(
        'netease-ncm',
        info.lyric or '',
        info.yrc_lyric or '',
        info.ytlrc_lyric or info.translated_lyric or '',
    )


def _fetchNeteasePublic(netease_id: str, cancel: threading.Event) -> _Raw | None:
    if not netease_id or cancel.is_set():
        return None
    header = {
        '__csrf': '',
        'appver': '8.0.0',
        'buildver': str(int(time.time())),
        'channel': '',
        'deviceId': '',
        'mobilename': '',
        'resolution': '1920x1080',
        'os': 'android',
        'osver': '',
        'requestId': f'{int(time.time() * 1000)}_{secrets.randbelow(1000):04d}',
        'versioncode': '140',
        'MUSIC_U': '',
    }
    payload = json.dumps(
        {
            'id': netease_id,
            'cp': 'false',
            'lv': '0',
            'kv': '0',
            'tv': '0',
            'rv': '0',
            'yv': '0',
            'ytv': '0',
            'yrv': '0',
            'csrf_token': '',
            'header': json.dumps(header, separators=(',', ':')),
        },
        separators=(',', ':'),
    )
    path = '/api/song/lyric/v1'
    digest = hashlib.md5(f'nobody{path}use{payload}md5forencrypt'.encode()).hexdigest()
    plaintext = f'{path}-36cd479b6b5-{payload}-36cd479b6b5-{digest}'.encode()
    encrypted = AES.new(b'e82ckenh8dichen8', AES.MODE_ECB).encrypt(pad(plaintext, 16))
    response = requests.post(
        'https://interface3.music.163.com/eapi/song/lyric/v1',
        data={'params': encrypted.hex().upper()},
        headers={
            'User-Agent': (
                'Mozilla/5.0 (Linux; Android 9; PCT-AL10) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/70.0.3538.64 '
                'HuaweiBrowser/10.0.3.311 Mobile Safari/537.36'
            ),
            'Referer': 'https://music.163.com/',
            'Cookie': '; '.join(f'{key}={value}' for key, value in header.items()),
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return None

    def block(name: str) -> str:
        value = data.get(name)
        return str(value.get('lyric') or '') if isinstance(value, dict) else ''

    return _neteaseRaw(
        'netease-public', block('lrc'), block('yrc'), block('ytlrc') or block('tlyric')
    )


def _qqSearch(query: str) -> list[_Pick]:
    response = requests.post(
        'https://u.y.qq.com/cgi-bin/musicu.fcg',
        json={
            'req_1': {
                'method': 'DoSearchForQQMusicDesktop',
                'module': 'music.search.SearchCgiService',
                'param': {
                    'num_per_page': '20',
                    'page_num': '1',
                    'query': query,
                    'search_type': 0,
                },
            }
        },
        headers=_QQ_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    songs = (
        response
        .json()
        .get('req_1', {})
        .get('data', {})
        .get('body', {})
        .get('song', {})
        .get('list')
        or []
    )
    return [
        _Pick(
            name=str(song.get('title') or ''),
            duration=_toInt(song.get('interval')) * 1000,
            payload={
                'id': str(song.get('id') or ''),
                'mid': str(song.get('mid') or ''),
            },
            artists=tuple(
                str(singer.get('name') or '')
                for singer in song.get('singer') or []
                if isinstance(singer, dict)
            ),
        )
        for parent in songs
        if isinstance(parent, dict)
        for song in [parent, *(parent.get('group') or [])]
        if isinstance(song, dict)
    ]


def _tagText(text: str, tag: str) -> str:
    match = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', text, re.DOTALL)
    if match is None:
        return ''
    body = match.group(1).strip()
    if body.startswith('<![CDATA['):
        body = body[len('<![CDATA[') :].rstrip()
        body = body.removesuffix(']]>')
    if not body or body.startswith('['):
        return body
    try:
        decrypted = qrcDecrypt(body)
    except (TypeError, ValueError, OSError, zlib.error):
        return ''
    if 'LyricContent' not in decrypted:
        return decrypted
    inner = re.search(r'LyricContent="(.*?)"', decrypted, re.DOTALL)
    return html.unescape(inner.group(1)) if inner else ''


def _qqBody(content: bytes) -> str:
    return content.decode('utf-8', 'replace').replace('<!--', '').replace('-->', '')


def _qqQrcLyrics(song_id: str) -> tuple[str, str]:
    response = requests.post(
        'https://c.y.qq.com/qqmusic/fcgi-bin/lyric_download.fcg',
        data={'version': '15', 'miniversion': '82', 'lrctype': '4', 'musicid': song_id},
        headers=_QQ_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    text = _qqBody(response.content)
    return _tagText(text, 'content'), _tagText(text, 'contentts')


def _qqFallbackLyrics(song_mid: str) -> tuple[str, str]:
    response = requests.post(
        'https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg',
        data={
            'callback': 'MusicJsonCallback_lrc',
            'jsonpCallback': 'MusicJsonCallback_lrc',
            'pcachetime': str(int(time.time() * 1000)),
            'songmid': song_mid,
            'format': 'jsonp',
            'g_tk': '5381',
            'loginUin': '0',
            'hostUin': '0',
            'inCharset': 'utf8',
            'outCharset': 'utf8',
            'notice': '0',
            'platform': 'yqq',
            'needNewCode': '0',
        },
        headers=_QQ_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    text = response.content.decode('utf-8', 'replace').strip()
    callback = 'MusicJsonCallback_lrc('
    if not text.startswith(callback):
        return '', ''
    data = json.loads(text[len(callback) :].rstrip(';').removesuffix(')'))
    if not isinstance(data, dict):
        return '', ''

    def decode(name: str) -> str:
        value = str(data.get(name) or '')
        try:
            return base64.b64decode(value).decode('utf-8', 'replace')
        except (TypeError, ValueError):
            return ''

    return decode('lyric'), decode('trans')


def _fetchQq(track: _Track, cancel: threading.Event) -> _Raw | None:
    matched = _searchTrack(_qqSearch, track, cancel)
    if matched is None or cancel.is_set():
        return None
    song = matched.payload
    try:
        lyrics, translated = _qqQrcLyrics(str(song.get('id') or ''))
    except (requests.RequestException, ValueError):
        lyrics, translated = '', ''
    if cancel.is_set():
        return None
    if not lyrics:
        lyrics, translated = _qqFallbackLyrics(str(song.get('mid') or ''))
    if not lyrics:
        return None
    return _textRaw('qq', parseAny(lyrics), translationTexts(translated))


def _kugouSearch(query: str) -> list[_Pick]:
    response = requests.get(
        'http://mobilecdn.kugou.com/api/v3/search/song',
        params={
            'format': 'json',
            'keyword': query,
            'page': '1',
            'pagesize': '20',
            'showtype': '1',
        },
        headers=_KUGOU_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    songs = (response.json().get('data') or {}).get('info') or []
    return [
        _Pick(
            name=str(song.get('songname') or ''),
            duration=_toInt(song.get('duration')) * 1000,
            payload=str(song.get('hash') or ''),
            artists=tuple(str(song.get('singername') or '').split('、')),
        )
        for parent in songs
        if isinstance(parent, dict)
        for song in [parent, *(parent.get('group') or [])]
        if isinstance(song, dict)
    ]


def _fetchKugou(track: _Track, cancel: threading.Event) -> _Raw | None:
    matched = _searchTrack(_kugouSearch, track, cancel)
    if matched is None or cancel.is_set():
        return None
    response = requests.get(
        'https://lyrics.kugou.com/search',
        params={
            'ver': '1',
            'man': 'yes',
            'client': 'pc',
            'keyword': f'{matched.name} {", ".join(matched.artists)}'.strip(),
            'duration': matched.duration,
            'hash': matched.payload,
        },
        headers=_KUGOU_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    candidates = response.json().get('candidates') or []
    candidate = next((item for item in candidates if isinstance(item, dict)), None)
    if not isinstance(candidate, dict) or cancel.is_set():
        return None
    response = requests.get(
        'https://lyrics.kugou.com/download',
        params={
            'ver': '1',
            'client': 'pc',
            'id': str(candidate.get('id') or ''),
            'accesskey': str(candidate.get('accesskey') or ''),
            'fmt': 'krc',
            'charset': 'utf8',
        },
        headers=_KUGOU_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    content = response.json().get('content')
    if not content:
        return None
    text = krcDecrypt(str(content))
    return _textRaw('kugou', parseKrc(text), krcTranslations(text))


def _sodaSearch(keyword: str) -> list[_Pick]:
    query = dict(_SODA_QUERY)
    query['q'] = keyword
    query['_rticket'] = str(_toInt(time.time() * 1000))
    response = requests.get(
        'https://api.qishui.com/luna/search/track',
        params=query,
        headers=_SODA_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    tracks: list[_Pick] = []
    for group in response.json().get('result_groups') or []:
        for item in group.get('data') or []:
            if not isinstance(item, dict):
                continue
            if (item.get('meta') or {}).get('item_type') != 'track':
                continue
            track = (item.get('entity') or {}).get('track')
            if not isinstance(track, dict):
                continue
            tracks.append(
                _Pick(
                    name=str(track.get('name') or ''),
                    duration=_toInt(track.get('duration')),
                    payload=str(track.get('id') or ''),
                    artists=tuple(
                        str(artist.get('name') or '')
                        for artist in track.get('artists') or []
                        if isinstance(artist, dict)
                    ),
                )
            )
    return tracks


def _fetchSoda(track: _Track, cancel: threading.Event) -> _Raw | None:
    matched = _searchTrack(_sodaSearch, track, cancel)
    if matched is None or not matched.payload or cancel.is_set():
        return None
    response = requests.get(
        'https://beta-luna.douyin.com/luna/h5/seo_track',
        params={'track_id': matched.payload, 'device_platform': 'web'},
        headers={'Accept': 'application/json', 'User-Agent': _WEB_UA},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    lyric = response.json().get('lyric') or {}
    if not isinstance(lyric, dict):
        return None
    translations = lyric.get('translations')
    chinese = translations.get('cn') if isinstance(translations, dict) else None
    return _textRaw(
        'soda',
        parseAny(str(lyric.get('content') or '')),
        translationTexts(chinese) if isinstance(chinese, str) else [],
    )


def _lrclibSearch(query: str) -> list[_Pick]:
    parts = query.split()
    if not parts:
        return []
    midpoint = len(parts) // 2
    params = {'track_name': query}
    if len(parts) >= 2:
        params = {
            'track_name': ' '.join(parts[:midpoint]),
            'artist_name': ' '.join(parts[midpoint:]),
        }
    response = requests.get(
        'https://lrclib.net/api/search',
        params=params,
        headers=_LRCLIB_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        response = requests.get(
            'https://lrclib.net/api/search',
            params={'track_name': query},
            headers=_LRCLIB_HEADERS,
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json()
    if not isinstance(results, list):
        return []
    return [
        _Pick(
            name=str(item.get('trackName') or ''),
            duration=_toInt(float(item.get('duration') or 0) * 1000),
            payload=item.get('id'),
            artists=tuple(
                re.split(r', | & | feat\. | ft\. ', str(item.get('artistName') or ''))
            ),
        )
        for item in results
        if isinstance(item, dict)
    ]


def _fetchLrclib(track: _Track, cancel: threading.Event) -> _Raw | None:
    matched = _searchTrack(_lrclibSearch, track, cancel)
    if matched is None or matched.payload is None or cancel.is_set():
        return None
    response = requests.get(
        f'https://lrclib.net/api/get/{matched.payload}',
        headers=_LRCLIB_HEADERS,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    entry = response.json()
    if not isinstance(entry, dict) or cancel.is_set():
        return None
    return _textRaw('lrclib', parseLrc(str(entry.get('syncedLyrics') or '')), [])


def _musixmatchRequest(
    request: str, params: dict[str, Any], cancel: threading.Event
) -> dict[str, Any]:
    global _musixmatch_last_request
    with _MUSIXMATCH_LOCK:
        if cancel.wait(max(0.0, 0.25 - (time.monotonic() - _musixmatch_last_request))):
            return {}
        merged = dict(params)
        merged.update({'app_id': _MUSIXMATCH_APP_ID, 't': uuid4().hex})
        response = requests.get(
            _MUSIXMATCH_BASE + request,
            params=merged,
            headers=_MUSIXMATCH_HEADERS,
            timeout=4,
        )
        _musixmatch_last_request = time.monotonic()
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError('Invalid Musixmatch response')
        header = (data.get('message') or {}).get('header') or {}
        if (
            header.get('status_code') == 401
            and str(header.get('hint')).lower() == 'captcha'
        ):
            raise PermissionError('Musixmatch requires captcha')
        if header.get('status_code') != 404:
            response.raise_for_status()
        return data


def _musixmatchToken(cancel: threading.Event) -> str:
    global _musixmatch_token
    with _MUSIXMATCH_TOKEN_LOCK:
        if _musixmatch_token:
            return _musixmatch_token
        data = _musixmatchRequest('token.get', {'user_language': 'en'}, cancel)
        message = data.get('message') or {}
        if (message.get('header') or {}).get('status_code') != 200:
            raise ValueError('Musixmatch token request failed')
        token = str((message.get('body') or {}).get('user_token') or '')
        if not token.strip() or token == 'null' or not token.strip('0'):
            raise ValueError('Invalid Musixmatch token')
        if cancel.is_set():
            return ''
        _musixmatch_token = token
        return token


def _musixmatchCall(
    request: str, params: dict[str, Any], cancel: threading.Event
) -> dict[str, Any]:
    global _musixmatch_token
    for attempt in range(5):
        if cancel.is_set():
            return {}
        try:
            token = _musixmatchToken(cancel)
            data = _musixmatchRequest(
                request, {**params, 'usertoken': token, 'format': 'json'}, cancel
            )
            header = (data.get('message') or {}).get('header') or {}
            status = header.get('status_code')
            if status in (200, 404):
                return data
            if status == 401 and str(header.get('hint')).lower() == 'renew':
                with _MUSIXMATCH_TOKEN_LOCK:
                    if _musixmatch_token == token:
                        _musixmatch_token = ''
        except (requests.RequestException, ValueError, TypeError) as error:
            _logger.debug('Musixmatch request %s failed: %r', request, error)
        if attempt < 4 and cancel.wait(min(0.5 * 2**attempt, 2.0)):
            return {}
    raise requests.RequestException('Musixmatch request failed after all retries')


def _fetchMusixmatch(track: _Track, cancel: threading.Event) -> _Raw | None:
    title_tokens = [
        part
        for part in re.split(r'[ \-_/,.()\[\]&]', track.title.lower())
        if len(part) > 1
    ]
    artist_tokens = [
        part
        for part in re.split(r'[ \-_/,.()\[\]&]', track.artist.lower())
        if len(part) > 1
    ]
    tracks: list[dict[str, Any]] = []
    for attempt in range(5):
        data = _musixmatchCall(
            'track.search',
            {
                'q_track': track.title,
                'q_artist': track.artist,
                'q_duration': track.duration // 1000 if track.duration > 0 else None,
                'page_size': '10',
                'page': '1',
                's_track_rating': 'desc',
            },
            cancel,
        )
        entries = ((data.get('message') or {}).get('body') or {}).get(
            'track_list'
        ) or []
        tracks = [
            item['track']
            for item in entries
            if isinstance(item, dict) and isinstance(item.get('track'), dict)
        ]
        if tracks and any(
            all(
                token in str(item.get('track_name') or '').lower()
                for token in title_tokens
            )
            and (
                not artist_tokens
                or any(
                    token in str(item.get('artist_name') or '').lower()
                    for token in artist_tokens
                )
            )
            for item in tracks
        ):
            break
        tracks = []
        if attempt < 4 and cancel.wait(min(0.2 * (attempt + 1), 0.8)):
            return None
    if not tracks or cancel.is_set():
        return None
    matched = tracks[0]
    track_id = str(matched.get('track_id') or '')
    if not track_id:
        return None
    calls: dict[str, Any] = {}
    for attempt in range(5):
        data = _musixmatchCall(
            'macro.subtitles.get',
            {
                'namespace': 'lyrics_richsynched',
                'optional_calls': 'track.richsync',
                'subtitle_format': 'lrc',
                'track_id': track_id,
                'f_subtitle_length_max_deviation': '40',
            },
            cancel,
        )
        calls = ((data.get('message') or {}).get('body') or {}).get('macro_calls') or {}
        actual = (
            ((calls.get('matcher.track.get') or {}).get('message') or {}).get('body')
            or {}
        ).get('track') or {}
        vanity = str(matched.get('commontrack_vanity_id') or '').strip().strip('/')
        actual_vanity = (
            str(actual.get('commontrack_vanity_id') or '').strip().strip('/')
        )
        if str(actual.get('track_id') or '') == track_id and (
            not vanity or vanity.lower() == actual_vanity.lower()
        ):
            break
        calls = {}
        if attempt < 4 and cancel.wait(min(0.2 * (attempt + 1), 0.8)):
            return None
    if not calls or cancel.is_set():
        return None
    richsync_message = (calls.get('track.richsync.get') or {}).get('message') or {}
    richsync = (richsync_message.get('body') or {}).get('richsync') or {}
    if (richsync_message.get('header') or {}).get('status_code') == 200:
        richsync_body = str(richsync.get('richsync_body') or '')
        if richsync_body:
            raw = _textRaw('musixmatch', parseRichsync(richsync_body), [])
            if raw is not None:
                return raw
    subtitle_message = (calls.get('track.subtitles.get') or {}).get('message') or {}
    if (subtitle_message.get('header') or {}).get('status_code') != 200:
        return None
    subtitles = subtitle_message.get('body') or {}
    subtitle_list = subtitles.get('subtitle_list') or []
    if not subtitle_list:
        return None
    subtitle = (subtitle_list[0].get('subtitle') or {}).get('subtitle_body') or ''
    return _textRaw('musixmatch', parseLrc(str(subtitle)), [])


def _tasks(
    track: _Track, netease_id: str
) -> list[tuple[str, Callable[..., _Raw | None], tuple[Any, ...]]]:
    return [
        ('netease-ncm', _fetchNeteaseNcm, (netease_id,)),
        ('netease-public', _fetchNeteasePublic, (netease_id,)),
        ('qq', _fetchQq, (track,)),
        ('kugou', _fetchKugou, (track,)),
        ('soda', _fetchSoda, (track,)),
        ('lrclib', _fetchLrclib, (track,)),
        ('musixmatch', _fetchMusixmatch, (track,)),
    ]


def iterLyricUpdates(
    title: str,
    artist: str | Sequence[str],
    netease_id: str,
    duration_ms: int,
    cached: Mapping[str, str] | None = None,
) -> Iterator[LyricCandidate]:
    artists = tuple(artist.split(', ')) if isinstance(artist, str) else tuple(artist)
    tasks = _tasks(_Track(title, artists, duration_ms), netease_id)
    cancel = threading.Event()
    executor = ThreadPoolExecutor(max_workers=len(tasks))
    original = _buildRaw(cached) if cached else None
    translation = original if original is not None and original.translations else None
    try:
        futures = {
            executor.submit(fetch, *args, cancel): name for name, fetch, args in tasks
        }
        pending = set(futures)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                name = futures[future]
                try:
                    raw = future.result()
                except Exception:
                    _logger.exception('lyric source %s failed', name)
                    continue
                if raw is None:
                    _logger.info('lyric source %s returned nothing', name)
                    continue
                changed = False
                if raw.translations and translation is None:
                    translation = raw
                    changed = True
                if (raw.lyric or raw.yrc_lyric) and (
                    original is None or (raw.has_word and not original.has_word)
                ):
                    original = raw
                    changed = True
                    if raw.translations:
                        translation = raw
                if not changed or original is None:
                    continue
                _logger.info(
                    'lyric update from %s: words=%s translation=%s',
                    original.source,
                    original.has_word,
                    translation.source if translation else '',
                )
                yield _assemble(original, translation)
            if original is not None and original.has_word and translation is not None:
                return
    finally:
        cancel.set()
        executor.shutdown(wait=False, cancel_futures=True)
