"""The NDA for new agents (docs/NDA.md): the text of NDA_Homes_in_Georgia_KA / KA-RU / KA-EN.pdf (2026-11-13) as data.

Each block has the Georgian text and its Russian and English translations; KA prints Georgian only, KA-RU / KA-EN print
two columns (Georgian left). «**…**» is bold. Fields filled by the system: {date} {name} {pid} {phone} {telegram} {role}.
To change the wording: edit here (all three languages), then new NDAs carry it — signed ones keep their own frozen text.
The draft's note «Draft for legal review…» is not part of the signed agreement."""

TITLE = {
    "ka": "კონფიდენციალურობისა და არაკონკურენციის შეთანხმება (NDA)",
    "ru": "СОГЛАШЕНИЕ О КОНФИДЕНЦИАЛЬНОСТИ И НЕКОНКУРЕНЦИИ (NDA)",
    "en": "NON-DISCLOSURE AND NON-COMPETE AGREEMENT (NDA)",
}
SUBTITLE = "Homes in Georgia / Rent in Tbilisi"

COMPANY = {"name": {"ka": "დავით ტიბელაშვილი", "ru": "Давид Тибелашвили", "en": "David Tibelashvili"},
           "personal_id": "01501158930"}

# (kind, {ka, ru, en}) — kind: h (heading), p (paragraph), li (numbered item of a list)
BLOCKS: list[tuple[str, dict]] = [
    ("p", {"ka": "ქ. თბილისი · თარიღი: {date}",
           "ru": "г. Тбилиси · Дата: {date}",
           "en": "Tbilisi · Date: {date}"}),
    ("p", {"ka": "**კომპანია:** დავით ტიბელაშვილი, პირადი ნომერი 01501158930, მოქმედი Homes in Georgia / Rent in "
                 "Tbilisi-ის სახელით (შემდგომში — „კომპანია“).",
           "ru": "**Компания:** Давид Тибелашвили, личный номер 01501158930, от имени Homes in Georgia / Rent in "
                 "Tbilisi (далее — «Компания»).",
           "en": "**Company:** David Tibelashvili, personal ID No. 01501158930, acting on behalf of Homes in Georgia / "
                 "Rent in Tbilisi (the “Company”)."}),
    ("p", {"ka": "**მიმღები:** {name}, პირადი ნომერი / პასპორტი {pid}, ტელეფონი {phone}, Telegram {telegram}, როლი "
                 "{role} (შემდგომში — „მიმღები“).",
           "ru": "**Получатель:** {name}, личный номер / паспорт {pid}, телефон {phone}, Telegram {telegram}, роль "
                 "{role} (далее — «Получатель»).",
           "en": "**Recipient:** {name}, personal ID / passport No. {pid}, phone {phone}, Telegram {telegram}, role "
                 "{role} (the “Recipient”)."}),
    ("h", {"ka": "1. კონფიდენციალური ინფორმაცია", "ru": "1. Конфиденциальная информация",
           "en": "1. Confidential Information"}),
    ("p", {"ka": "ყველაფერი, რაც მიმღებმა კომპანიასთან მუშაობისას შეიტყო: სამუშაო პირობები და ანაზღაურების სისტემა "
                 "„90/10“; სტატისტიკა და შემოსავლები; მესაკუთრეების, ობიექტებისა და კლიენტების ბაზები (კონტაქტების, "
                 "დოკუმენტების, ფოტოებისა და უკვე გაქირავებული ობიექტების ჩათვლით); CRM, მისი კოდი და კომპანიის ყველა "
                 "გენერატორი; სამუშაო პროცესები, სკრიპტები და ხელშეკრულებების შაბლონები; ლოგინები და პაროლები. აღნიშვნა "
                 "„კონფიდენციალური“ საჭირო არ არის.",
           "ru": "Всё, что Получатель узнал в работе с Компанией: условия и система вознаграждения «90/10»; статистика и "
                 "доходы; базы собственников, объектов и клиентов (включая контакты, документы, фото и сданные объекты); "
                 "CRM, код и все генераторы Компании; рабочие процессы, скрипты и шаблоны договоров; логины и пароли. "
                 "Пометка «конфиденциально» не требуется.",
           "en": "Everything the Recipient learns while working with the Company: the terms and the “90/10” compensation "
                 "system; statistics and revenue; databases of owners, properties and clients (including contacts, "
                 "documents, photos and properties already rented); the CRM, its code and all Company generators; work "
                 "processes, scripts and contract templates; logins and passwords. No “confidential” marking is required."}),
    ("h", {"ka": "2. აკრძალვები", "ru": "2. Запреты", "en": "2. Restrictions"}),
    ("p", {"ka": "მიმღებს ეკრძალება:", "ru": "Получатель не вправе:", "en": "The Recipient shall not:"}),
    ("li", {"ka": "ამ ინფორმაციის ვინმესთვის გამჟღავნება.", "ru": "Раскрывать эту информацию кому-либо.",
            "en": "Disclose this information to anyone."}),
    ("li", {"ka": "მისი გამოყენება საკუთარი ან მესამე პირების ინტერესებისთვის, მათ შორის კომპანიის „90/10“ სისტემისა და "
                  "სტატისტიკის გამოყენება.",
            "ru": "Использовать её для себя или третьих лиц, в том числе применять систему «90/10» и статистику Компании.",
            "en": "Use it for themselves or third parties, including applying the Company’s “90/10” system and statistics."}),
    ("li", {"ka": "ბაზების, კონტაქტებისა და კოდის კოპირება და გადაგზავნა პირად ჩატებში, მოწყობილობებსა და ღრუბლოვან "
                  "საცავებში.",
            "ru": "Копировать и пересылать базы, контакты и код в личные чаты, устройства и облака.",
            "en": "Copy or forward databases, contacts or code to personal chats, devices or cloud storage."}),
    ("li", {"ka": "კომპანიის მესაკუთრეებთან და კლიენტებთან დაკავშირება კომპანიის გვერდის ავლით.",
            "ru": "Связываться с собственниками и клиентами Компании в обход неё.",
            "en": "Contact the Company’s owners and clients bypassing the Company."}),
    ("li", {"ka": "კომპანიის თანამშრომლებისა და აგენტების გადაბირება.", "ru": "Переманивать сотрудников и агентов Компании.",
            "en": "Poach the Company’s employees and agents."}),
    ("li", {"ka": "კომპანიის ნამუშევრებზე დაფუძნებული, უძრავი ქონების გაქირავების კონკურენტული ბიზნესის ან "
                  "რიელტორებისთვის CRM-ის შექმნა ან მასში დახმარება.",
            "ru": "Открывать или помогать конкурирующему бизнесу по аренде недвижимости или CRM для риелторов, основанному "
                  "на наработках Компании.",
            "en": "Open or assist a competing property rental business or realtor CRM based on the Company’s work."}),
    ("p", {"ka": "ყველაფერი, რასაც მიმღები კომპანიისთვის ქმნის (კოდი, დიზაინი, კონტენტი, იდეები), ეკუთვნის კომპანიას.",
           "ru": "Всё, что Получатель создаёт для Компании (код, дизайн, контент, идеи), принадлежит Компании.",
           "en": "Everything the Recipient creates for the Company (code, design, content, ideas) belongs to the Company."}),
    ("p", {"ka": "**კომპანიის CRM-ში ატვირთული ყველა ფოტო, მიუხედავად იმისა, თუ ვინ გადაიღო, არის Homes in Georgia-ს "
                 "საკუთრება.** მიმღები კომპანიას გადასცემს ამ ფოტოებზე ყველა უფლებას და უფლება არ აქვს, გამოიყენოს, "
                 "გამოაქვეყნოს ან გადასცეს ისინი კომპანიის წერილობითი თანხმობის გარეშე, მათ შორის თანამშრომლობის "
                 "დასრულების შემდეგ.",
           "ru": "**Все фотографии, загруженные в CRM Компании, кем бы они ни были сделаны, являются собственностью "
                 "Homes in Georgia.** Получатель передаёт Компании все права на эти фотографии и не вправе использовать, "
                 "публиковать или передавать их без письменного согласия Компании, в том числе после окончания "
                 "сотрудничества.",
           "en": "**All photos uploaded to the Company’s CRM, regardless of who took them, are the property of Homes in "
                 "Georgia.** The Recipient transfers to the Company all rights to these photos and may not use, publish "
                 "or transfer them without the Company’s written consent, including after the cooperation ends."}),
    ("h", {"ka": "3. ვადა", "ru": "3. Срок", "en": "3. Term"}),
    ("p", {"ka": "შეთანხმება ძალაშია ხელმოწერის მომენტიდან, **თანამშრომლობის მთელი პერიოდის განმავლობაში და კომპანიის "
                 "სამუშაო ჩატიდან მიმღების ამოშლიდან არანაკლებ 2 წლის განმავლობაში.** პერსონალურ მონაცემებთან, კოდთან "
                 "და პაროლებთან დაკავშირებული ვალდებულებები უვადოა.",
           "ru": "Соглашение действует с момента подписания, **весь срок сотрудничества и не менее 2 лет после отключения "
                 "Получателя из рабочего чата Компании.** Обязательства по персональным данным, коду и паролям — "
                 "бессрочно.",
           "en": "This Agreement is effective from signing, **for the entire period of cooperation and for no less than "
                 "2 years after the Recipient is removed from the Company’s work chat.** Obligations regarding personal "
                 "data, code and passwords are perpetual."}),
    ("h", {"ka": "4. პასუხისმგებლობა", "ru": "4. Ответственность", "en": "4. Liability"}),
    ("p", {"ka": "**ყოველი დარღვევისთვის მიმღები იხდის პირგასამტეხლოს თანამშრომლობის მთელი პერიოდის განმავლობაში "
                 "კომპანიისგან მიღებული მთლიანი შემოსავლის 100%-ის ოდენობით, მაგრამ ნებისმიერ შემთხვევაში არანაკლებ "
                 "10 000 (ათი ათასი) აშშ დოლარისა** (გამოიყენება ორ თანხას შორის უდიდესი), მოთხოვნიდან 10 დღის ვადაში. "
                 "პირგასამტეხლოს გარდა, მიმღები ანაზღაურებს ზიანს, მათ შორის საკომისიოს კომპანიის გვერდის ავლით "
                 "წაყვანილ ობიექტებსა და კლიენტებზე.",
           "ru": "За каждое нарушение Получатель платит неустойку в размере **100% всего дохода, полученного от Компании "
                 "за весь период сотрудничества, но в любом случае не менее 10 000 (десяти тысяч) долларов США** "
                 "(применяется бо́льшая из двух сумм), в течение 10 дней после требования. Сверх неустойки Получатель "
                 "возмещает ущерб, включая комиссию по уведённым объектам и клиентам.",
           "en": "For each breach, the Recipient shall pay a penalty equal **to 100% of all income received from the "
                 "Company over the entire period of cooperation, but in any case not less than USD 10,000 (ten "
                 "thousand)** (whichever is greater), within 10 days of demand. In addition to the penalty, the "
                 "Recipient shall compensate damages, including commission on properties and clients diverted from the "
                 "Company."}),
    ("h", {"ka": "5. სხვა დებულებები", "ru": "5. Прочее", "en": "5. Miscellaneous"}),
    ("p", {"ka": "კომპანიის ელექტრონული ხელმოწერის სისტემით შესრულებული ხელმოწერა უტოლდება ხელით შესრულებულ "
                 "ხელმოწერას; მტკიცებულებაა სისტემის ჟურნალი (დრო, IP, ტელეფონი/Telegram, დადასტურების კოდი). "
                 "გამოიყენება საქართველოს კანონმდებლობა; დავებს განიხილავს თბილისის საქალაქო სასამართლო.",
           "ru": "Подпись через систему электронной подписи Компании равнозначна собственноручной; доказательство — "
                 "журнал системы (время, IP, телефон/Telegram, код подтверждения). Право — Грузии, споры — Тбилисский "
                 "городской суд.",
           "en": "Signing through the Company’s electronic signature system is equivalent to a handwritten signature; "
                 "evidence is the system log (time, IP, phone/Telegram, confirmation code). Governing law: Georgia; "
                 "disputes: Tbilisi City Court."}),
]

# the bilingual versions only
LANG_CLAUSE = {
    "KA-RU": {"ka": "შეთანხმება შედგენილია ქართულ და რუსულ ენებზე. შეუსაბამობის შემთხვევაში უპირატესობა ენიჭება ქართულ "
                    "ტექსტს.",
              "ru": "Соглашение составлено на грузинском и русском языках. При расхождении приоритет имеет грузинский "
                    "текст."},
    "KA-EN": {"ka": "შეთანხმება შედგენილია ქართულ და ინგლისურ ენებზე. შეუსაბამობის შემთხვევაში უპირატესობა ენიჭება "
                    "ქართულ ტექსტს.",
              "en": "This Agreement is made in Georgian and English. In case of discrepancy, the Georgian text prevails."},
}

SIGNATURES = {
    "title": {"ka": "ხელმოწერები", "ru": "Подписи сторон", "en": "Signatures"},
    "company": {"ka": "კომპანია", "ru": "Компания", "en": "Company"},
    "recipient": {"ka": "მიმღები", "ru": "Получатель", "en": "Recipient"},
    "name": {"ka": "სახელი, გვარი", "ru": "ФИО", "en": "Full name"},
    "signature": {"ka": "ხელმოწერა", "ru": "Подпись", "en": "Signature"},
    "time": {"ka": "თარიღი და დრო", "ru": "Дата и время", "en": "Date and time"},
    "pending": {"ka": "[ელ. ხელმოწერა]", "ru": "[эл. подпись]", "en": "[e-signature]"},
    "auto": {"ka": "[ავტომატურად]", "ru": "[авто]", "en": "[auto]"},
}
