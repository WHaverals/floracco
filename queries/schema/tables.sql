CREATE TABLE `admin` (
  `login` varchar(10) NOT NULL,
  `password` varchar(20) DEFAULT NULL,
  `name` varchar(50) DEFAULT NULL,
  `email` varchar(50) DEFAULT NULL,
  `admin_level` int(2) NOT NULL,
  PRIMARY KEY (`login`)
);
CREATE TABLE `contract` (
  `contract_id` int(11) NOT NULL ,
  `archive` varchar(20) DEFAULT NULL,
  `series` varchar(150) DEFAULT NULL,
  `folder` varchar(20) DEFAULT NULL,
  `folio` varchar(20) DEFAULT NULL,
  `registration_date` date NOT NULL,
  `firm_name` varchar(100) DEFAULT NULL,
  `pl_discretion` tinyint(1) DEFAULT 0,
  `economic_sector` int(11) DEFAULT NULL,
  `economic_sector_db` tinyint(1) NOT NULL DEFAULT 0,
  `ec_discretion` tinyint(1) DEFAULT 0,
  `start_date` date DEFAULT NULL,
  `duration_months` int(11) DEFAULT NULL,
  `automatic_renewal` tinyint(1) DEFAULT 0,
  `automatic_renewal_months` int(11) DEFAULT NULL,
  `clauses` tinyint(1) DEFAULT 0,
  `administrators` tinyint(1) NOT NULL DEFAULT 0,
  `total` int(11) DEFAULT NULL,
  `numerical_discrepancy` tinyint(1) NOT NULL DEFAULT 0,
  `additional_docs` tinyint(1) NOT NULL DEFAULT 0,
  `currency_id` int(11) DEFAULT NULL,
  `document` longtext DEFAULT NULL,
  `temp` tinyint(1) DEFAULT 1,
  PRIMARY KEY (`contract_id`)
) ;
CREATE TABLE `contract_place` (
  `place_id` int(11) NOT NULL DEFAULT 0,
  `contract_id` int(11) NOT NULL DEFAULT 0,
  `address` varchar(100) DEFAULT NULL,
  `place_db` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`place_id`,`contract_id`)
/*   KEY `i_place_id` (`place_id`), */
/*   KEY `i_contract_id` (`contract_id`) */
) ;
CREATE TABLE `currency` (
  `currency_id` int(11) NOT NULL ,
  `currency` varchar(200) NOT NULL,
  `start_date` date DEFAULT NULL,
  `end_date` date DEFAULT NULL,
  `ratio` int(11) DEFAULT NULL,
  PRIMARY KEY (`currency_id`)
  /* UNIQUE KEY `currency` (`currency`) */
) ;
CREATE TABLE `economic_activity` (
  `ec_activity_id` int(11) NOT NULL ,
  `activity` varchar(200) NOT NULL,
  PRIMARY KEY (`ec_activity_id`)
  /* UNIQUE KEY `activity` (`activity`) */
) ;
CREATE TABLE `investment` (
  `investment_id` int(11) NOT NULL ,
  `contract_id` int(11) NOT NULL DEFAULT 0,
  `type` TEXT DEFAULT NULL,
  `partnership_name` varchar(200) DEFAULT NULL,
  `investment_cash` int(20) DEFAULT NULL,
  `investment_non_cash` varchar(200) DEFAULT NULL,
  `temp` tinyint(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (`investment_id`)
  /* KEY `investment_contract_id` (`contract_id`) */
) ;
CREATE TABLE `investor` (
  `investor_id` int(11) NOT NULL ,
  `person_id` int(11) NOT NULL,
  `contract_id` int(11) NOT NULL,
  `title` int(11) DEFAULT NULL,
  `title_husband` int(11) DEFAULT NULL,
  `title_grandfather` int(11) DEFAULT NULL,
  `title_father_mother` int(11) DEFAULT NULL,
  `husband_first_name` varchar(20) DEFAULT NULL,
  `husband_last_name` varchar(20) DEFAULT NULL,
  `place_of_residence` int(11) DEFAULT NULL,
  `place_of_origin` int(11) DEFAULT NULL,
  `profession` varchar(100) DEFAULT NULL,
  `citizen_florence` tinyint(1) NOT NULL DEFAULT 0,
  `is_widow` tinyint(1) DEFAULT 0,
  `is_guardian` tinyint(1) NOT NULL DEFAULT 0,
  `guardian_of` varchar(200) DEFAULT NULL,
  `is_jewish` tinyint(1) DEFAULT 0,
  `jewish_db` tinyint(1) NOT NULL DEFAULT 0,
  `is_convert` tinyint(1) DEFAULT 0,
  `via_proxy` tinyint(1) DEFAULT 0,
  `is_joint` tinyint(1) NOT NULL DEFAULT 0,
  `heirs` tinyint(4) NOT NULL DEFAULT 0,
  `heirs_of` tinyint(4) NOT NULL DEFAULT 0,
  `and_c` tinyint(4) NOT NULL DEFAULT 0,
  `temp` tinyint(1) DEFAULT 1,
  PRIMARY KEY (`investor_id`)
  /* KEY `investor_pid` (`person_id`), */
  /* KEY `investor_cid` (`contract_id`), */
  /* KEY `investor_title` (`title`), */
  /* KEY `investor_gtitle` (`title_grandfather`), */
  /* KEY `investor_ftitle` (`title_father_mother`), */
  /* KEY `investor_por` (`place_of_residence`) */
) ;
CREATE TABLE `investor_group` (
  `investor_id` int(11) NOT NULL DEFAULT 0,
  `investment_id` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`investor_id`,`investment_id`)
  /* KEY `i_investor_id` (`investor_id`), */
  /* KEY `i_investment_id` (`investment_id`) */
) ;
CREATE TABLE `person` (
  `person_id` int(11) NOT NULL ,
  `first_name` varchar(100) DEFAULT NULL,
  `father_mother` varchar(50) DEFAULT NULL,
  `grandfather` varchar(20) DEFAULT NULL,
  `last_name` varchar(200) DEFAULT NULL,
  `nickname` varchar(50) DEFAULT NULL,
  `is_woman` tinyint(1) DEFAULT 0,
  `temp` tinyint(1) DEFAULT 1,
  PRIMARY KEY (`person_id`)
) ;
CREATE TABLE `place` (
  `place_id` int(11) NOT NULL ,
  `place_name` varchar(50) NOT NULL,
  PRIMARY KEY (`place_id`)
  /* UNIQUE KEY `place_name` (`place_name`) */
) ;
CREATE TABLE `sub_contract` (
  `contract_id` int(11) NOT NULL ,
  `archive` varchar(20) NOT NULL,
  `series` varchar(150) NOT NULL,
  `folder` varchar(20) NOT NULL,
  `folio` varchar(20) NOT NULL,
  `registration_date` date NOT NULL,
  `end_date` date DEFAULT NULL,
  `renewal_months` int(11) DEFAULT NULL,
  `sub_firm_name` varchar(100) DEFAULT NULL,
  `document` longtext DEFAULT NULL,
  `sub_type` TEXT DEFAULT NULL,
  `main_contract_id` int(11) NOT NULL,
  `temp` tinyint(1) DEFAULT 1,
  PRIMARY KEY (`contract_id`)
  /* KEY `sub_contract_main_id` (`main_contract_id`) */
) ;
CREATE TABLE `title` (
  `title_id` int(11) NOT NULL ,
  `title_name` varchar(150) NOT NULL,
  PRIMARY KEY (`title_id`)
  /* UNIQUE KEY `title_name` (`title_name`) */
) ;
